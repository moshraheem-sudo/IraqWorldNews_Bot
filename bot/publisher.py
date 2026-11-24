"""Main runner for scraping news and publishing to Telegram Channel."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Set

from bot.config import AppConfig, load_app_config
from bot.fetcher import create_http_session, fetch_url
from bot.parser import NewsItem, parse_rss_feed, parse_telegram_web, parse_x_list
from bot.storage import StorageManager
from bot.telegram_client import TelegramClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("iraq_world_news_bot")


def normalize_title_for_dedup(title: str) -> str:
    """Normalize news headlines for cross-source deduplication."""
    if not title or len(title.strip()) < 8:
        return ""
    t = title.strip()
    for prefix in ["عاجل :", "عاجل -", "عاجل |", "عاجل:", "عاجل", "هام:", "هام |", "هام", "بالوثيقة", "بالفيديو", "تنويه"]:
        if t.startswith(prefix):
            t = t[len(prefix):].strip()
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"[\u064B-\u0652\u0670]", "", t)
    t = re.sub(r"[إأآا]", "ا", t)
    t = re.sub(r"[ىي]", "ي", t)
    t = re.sub(r"ة", "ه", t)
    words = [w for w in t.split() if len(w) > 2]
    if len(words) < 3:
        return ""
    return " ".join(words[:6])


def run_cycle(
    config_path: Path,
    storage_path: Path,
    dry_run: bool = False,
    token_override: str | None = None,
    chat_id_override: str | None = None,
) -> int:
    config = load_app_config(config_path, token_override=token_override, chat_id_override=chat_id_override)
    storage = StorageManager(storage_path)
    telegram = TelegramClient(config.telegram_bot_token, config.telegram_chat_id)

    if not dry_run:
        if not telegram.is_configured():
            logger.error(
                "TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID are not set! "
                "Please configure them in your environment or GitHub Secrets."
            )
            return 1
        # Verify access to target channel before doing the heavy scraping work
        telegram.verify_chat()

    session = create_http_session(config.user_agent)
    all_new_items: List[NewsItem] = []
    seen_in_batch_titles: Set[str] = set()

    logger.info("Starting scrape across %d configured sources...", len(config.sources))

    try:
        for source in config.sources:
            logger.info("Processing source: %s (%s)", source.name, source.url)
            target_url = source.url
            if source.source_type == "x_list":
                match = re.search(r"(\d{6,})", source.url) or re.search(r"(\d{6,})", source.username)
                if match:
                    target_url = f"https://syndication.twitter.com/srv/timeline-list/list-id/{match.group(1)}"

            content = fetch_url(session, target_url, timeout=config.request_timeout)
            if not content:
                logger.warning("No content returned for %s", source.name)
                continue

            try:
                if source.source_type == "rss":
                    items = parse_rss_feed(content, source)
                elif source.source_type == "x_list":
                    items = parse_x_list(content, source)
                else:
                    items = parse_telegram_web(content, source)

                logger.info("Extracted %d items from %s", len(items), source.name)

                # Filter unsent items by ID and by headline fingerprint (cross-source duplicate protection)
                for item in items:
                    if storage.is_sent(item.id):
                        continue

                    title_key = normalize_title_for_dedup(item.title)
                    if title_key:
                        if storage.is_title_sent(title_key) or title_key in seen_in_batch_titles:
                            logger.info("Skipping cross-source duplicate headline: %s", item.title[:50])
                            storage.mark_sent(item.id, title_key)
                            continue
                        seen_in_batch_titles.add(title_key)

                    all_new_items.append(item)

            except Exception as e:
                logger.error("Error parsing source %s: %s", source.name, e)

    finally:
        session.close()

    logger.info("Total unseen candidates discovered: %d", len(all_new_items))

    if not all_new_items:
        logger.info("No new news found in this cycle. Channel is up to date.")
        storage.save()
        return 0

    # Protection against backlog flooding:
    # Configurable max batch size per run (default to 100 posts)
    max_posts_env = os.environ.get("MAX_POSTS_PER_RUN", "").strip()
    MAX_POSTS_PER_RUN = int(max_posts_env) if max_posts_env.isdigit() else 100

    # Sort all candidates by published_at (oldest to newest)
    all_new_items.sort(key=lambda x: x.published_at)

    if storage.is_first_run or len(all_new_items) > MAX_POSTS_PER_RUN:
        logger.info(
            "Found %d items. Limiting this run to the %d latest items and marking the rest as sent.",
            len(all_new_items),
            MAX_POSTS_PER_RUN,
        )
        # Take the most recent items
        items_to_send = all_new_items[-MAX_POSTS_PER_RUN:]
        # Mark all older overflow items as sent in storage so backlog never accumulates
        for item in all_new_items[:-MAX_POSTS_PER_RUN]:
            title_key = normalize_title_for_dedup(item.title)
            storage.mark_sent(item.id, title_key)
    else:
        items_to_send = all_new_items

    logger.info("Ready to publish %d new items to Telegram...", len(items_to_send))

    published_count = 0
    consecutive_failures = 0

    for idx, item in enumerate(items_to_send, 1):
        title_key = normalize_title_for_dedup(item.title)
        logger.info("[%d/%d] Publishing: [%s] %s", idx, len(items_to_send), item.source_name, item.title[:50])

        if dry_run:
            logger.info("[DRY RUN] Would send item %s to %s", item.id, config.telegram_chat_id)
            storage.mark_sent(item.id, title_key)
            published_count += 1
        else:
            success = telegram.send_news(item)
            if success:
                storage.mark_sent(item.id, title_key)
                published_count += 1
                consecutive_failures = 0
                logger.info("Successfully published %s", item.id)
            else:
                consecutive_failures += 1
                logger.error("Failed to send %s to Telegram.", item.id)
                # If 4 consecutive failures occur, stop this cycle early
                if consecutive_failures >= 4:
                    logger.warning("Aborting remaining posts in this cycle due to repeated send failures.")
                    break

            # Polite delay between messages to respect Telegram limits
            time.sleep(config.delay_between_posts)

    storage.save()
    logger.info("Cycle completed. Published %d new items.", published_count)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Iraq & World News Telegram Bot Publisher")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/sources.json"),
        help="Path to sources.json",
    )
    parser.add_argument(
        "--storage",
        type=Path,
        default=Path("data/sent_posts.json"),
        help="Path to sent_posts.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without sending messages to Telegram",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Telegram bot token override",
    )
    parser.add_argument(
        "--chat-id",
        type=str,
        default=None,
        help="Telegram chat ID override",
    )

    args = parser.parse_args()
    return run_cycle(
        config_path=args.config,
        storage_path=args.storage,
        dry_run=args.dry_run,
        token_override=args.token,
        chat_id_override=args.chat_id,
    )


if __name__ == "__main__":
    sys.exit(main())
