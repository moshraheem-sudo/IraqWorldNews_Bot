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
        self.chat_id = str(chat_id).strip()
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        self._init_session()

    def _init_session(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NewsBot/1.0",
            "Connection": "close",
        })

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def verify_chat(self) -> bool:
        """Verify that the bot token is valid and can access the target chat/channel."""
        if not self.is_configured():
            logger.error("Cannot verify chat: Token or Chat ID is missing.")
            return False

        endpoint = f"{self.api_url}/getChat"
        try:
            resp = self.session.post(endpoint, json={"chat_id": self.chat_id}, timeout=30)
            data = resp.json()
            if data.get("ok"):
                res = data.get("result", {})
                logger.info(
                    "Chat verified successfully! Title: '%s', Type: '%s', ID: %s",
                    res.get("title", "N/A"),
                    res.get("type", "N/A"),
                    res.get("id"),
                )
                return True
            else:
                logger.error(
                    "getChat failed for chat_id='%s'! Telegram API returned (%s): %s",
                    self.chat_id,
                    data.get("error_code"),
                    data.get("description"),
                )
                return False
        except Exception as e:
            logger.error("Exception during chat verification: %s", e)
            return False

    def send_news(self, item: NewsItem) -> bool:
        if not self.is_configured():
            logger.error("Telegram bot token or chat ID is missing! Cannot send post.")
            return False

        caption = format_news_post(item, max_body_len=950)

        # 1. Attempt to send as playable Video if item has a video URL
        if item.video_url and item.video_url.startswith("http"):
            if self._send_video(item.video_url, caption):
                return True
            logger.info("sendVideo failed or timed out for %s, trying photo/text fallback.", item.id)

        # 2. Attempt to send as Photo with caption if item has an image
        if item.image_url and item.image_url.startswith("http"):
            if self._send_photo(item.image_url, caption):
                return True
            logger.info("sendPhoto failed or timed out for %s, falling back to formatted text.", item.id)

        # 3. Fallback / Text post with preview disabled
        text = format_news_post(item, max_body_len=3500)
        return self._send_text(text)

    def _send_video(self, video_url: str, caption: str) -> bool:
        endpoint = f"{self.api_url}/sendVideo"
        payload = {
            "chat_id": self.chat_id,
            "video": video_url,
            "caption": caption,
            "parse_mode": "HTML",
            "supports_streaming": True,
        }
        success = self._post_with_retry(endpoint, payload, timeout=35)
        if not success and self.chat_id.isdigit() and not self.chat_id.startswith("-100"):
            alt_chat_id = f"-100{self.chat_id}"
            payload["chat_id"] = alt_chat_id
            if self._post_with_retry(endpoint, payload, timeout=35):
                self.chat_id = alt_chat_id
                return True
        return success

    def _send_photo(self, photo_url: str, caption: str) -> bool:
        endpoint = f"{self.api_url}/sendPhoto"
        payload = {
            "chat_id": self.chat_id,
            "photo": photo_url,
            "caption": caption,
            "parse_mode": "HTML",
        }
        success = self._post_with_retry(endpoint, payload, timeout=25)
        if not success and self.chat_id.isdigit() and not self.chat_id.startswith("-100"):
            alt_chat_id = f"-100{self.chat_id}"
            payload["chat_id"] = alt_chat_id
            if self._post_with_retry(endpoint, payload, timeout=25):
                self.chat_id = alt_chat_id
                return True
        return success

    def _send_text(self, text: str) -> bool:
        endpoint = f"{self.api_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        success = self._post_with_retry(endpoint, payload)
        # If chat not found and chat_id is positive digits, try prefixing -100 (Telegram channel ID format)
        if not success and self.chat_id.isdigit() and not self.chat_id.startswith("-100"):
            alt_chat_id = f"-100{self.chat_id}"
            logger.info("Retrying with channel prefix ID: %s", alt_chat_id)
            payload["chat_id"] = alt_chat_id
            if self._post_with_retry(endpoint, payload):
                self.chat_id = alt_chat_id  # update to working ID
                return True
        return success

    def _post_with_retry(self, endpoint: str, payload: dict, retries: int = 1, timeout: int = 40) -> bool:
        for attempt in range(retries + 1):
            try:
                resp = self.session.post(endpoint, json=payload, timeout=timeout)
                data = resp.json()

                if data.get("ok"):
                    return True

                error_code = data.get("error_code")
                description = data.get("description", "Unknown error")

                # Handle rate limiting (429)
                if error_code == 429:
                    retry_after = data.get("parameters", {}).get("retry_after", 10)
                    logger.warning("Telegram Rate Limit hit (retry_after=%d). Sleeping...", retry_after)
                    self._init_session()
                    time.sleep(min(retry_after + 1, 35))
                    continue

                logger.error(
                    "Telegram API response: code=%s, desc='%s', chat_id='%s'",
                    error_code,
                    description,
                    payload.get("chat_id"),
                )
                return False

            except requests.RequestException as e:
                logger.warning("Network request error on attempt %d: %s", attempt + 1, e)
                self._init_session()
                time.sleep(2)

        return False
