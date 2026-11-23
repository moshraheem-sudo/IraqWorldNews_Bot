"""Telegram Bot API Client for posting news items."""

from __future__ import annotations

import html
import logging
import time
from typing import Optional

import requests

from bot.parser import NewsItem

logger = logging.getLogger(__name__)


def escape_html(text: str) -> str:
    return html.escape(text, quote=False)


def format_news_post(item: NewsItem, max_body_len: int = 1500) -> str:
    """Format a NewsItem into a clean, professional Arabic Telegram HTML message."""
    category_header = f"🔴 <b>{escape_html(item.source_name)}</b>"
    if "عراق" in item.category:
        category_tag = f"#{item.hashtag.replace(' ', '_')} #أخبار_العراق"
    else:
        category_tag = f"#{item.hashtag.replace(' ', '_')} #أخبار_العالم"

    content = item.content.strip()
    # Remove redundant title if content starts with it
    if item.title and content.startswith(item.title):
        body = content[len(item.title) :].strip()
    else:
        body = content

    if len(body) > max_body_len:
        body = body[:max_body_len].rsplit(" ", 1)[0] + "…"

    lines = [
        f"{category_header}",
        "",
    ]

    if item.title:
        lines.append(f"📌 <b>{escape_html(item.title)}</b>")
        lines.append("")

    if body and body != item.title:
        lines.append(escape_html(body))
        lines.append("")

    footer_parts = []
    if item.original_url:
        footer_parts.append(f"🔗 <a href=\"{escape_html(item.original_url)}\">رابط الخبر الأصلي</a>")
    footer_parts.append(f"🏷️ {category_tag}")

    lines.append(" | ".join(footer_parts))

    return "\n".join(lines).strip()


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token.strip()
        self.chat_id = chat_id.strip()
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.session = requests.Session()

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_news(self, item: NewsItem) -> bool:
        if not self.is_configured():
            logger.error("Telegram bot token or chat ID is missing! Cannot send post.")
            return False

        # Attempt to send with photo if image_url exists
        if item.image_url:
            caption = format_news_post(item, max_body_len=800)
            if len(caption) <= 1024:
                success = self._send_photo(item.image_url, caption)
                if success:
                    return True
                logger.info("sendPhoto failed or image invalid, falling back to text message.")

        # Text fallback or normal text post
        text = format_news_post(item, max_body_len=3500)
        return self._send_text(text)

    def _send_text(self, text: str) -> bool:
        endpoint = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }
        return self._post_with_retry(endpoint, payload)

    def _send_photo(self, photo_url: str, caption: str) -> bool:
        endpoint = f"{self.api_url}/sendPhoto"
        payload = {
            "chat_id": self.chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML",
        }
        return self._post_with_retry(endpoint, payload)

    def _post_with_retry(self, endpoint: str, payload: dict, retries: int = 2) -> bool:
        for attempt in range(retries + 1):
            try:
                resp = self.session.post(endpoint, json=payload, timeout=25)
                data = resp.json()

                if data.get("ok"):
                    return True

                error_code = data.get("error_code")
                description = data.get("description", "Unknown error")

                # Handle rate limiting (429)
                if error_code == 429:
                    retry_after = data.get("parameters", {}).get("retry_after", 5)
                    logger.warning("Telegram Rate Limit hit. Sleeping for %d seconds...", retry_after)
                    time.sleep(retry_after + 1)
                    continue

                logger.error("Telegram API error (%s): %s", error_code, description)
                return False

            except requests.RequestException as e:
                logger.warning("Network request error on attempt %d: %s", attempt + 1, e)
                time.sleep(2)

        return False
