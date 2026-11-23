"""Configuration loader and environment settings for Iraq & World News Bot."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class SourceConfig:
    id: str
    source_type: str
    name: str
    username: str
    url: str
    category: str
    hashtag: str = "أخبار"
    language: str = "ar"
    enabled: bool = True
    max_posts: int = 15


@dataclass
class AppConfig:
    telegram_bot_token: str
    telegram_chat_id: str
    first_run_max_posts: int = 3
    delay_between_posts: float = 2.0
    request_timeout: int = 10
    user_agent: str = "IraqWorldNewsBot/1.0"
    sources: List[SourceConfig] = field(default_factory=list)


def load_app_config(
    config_path: Path,
    token_override: Optional[str] = None,
    chat_id_override: Optional[str] = None,
) -> AppConfig:
    bot_token = (token_override or os.environ.get("TELEGRAM_BOT_TOKEN", "")).strip().lstrip("\ufeff\u200e\u200f\u202a\u202b\u202c")
    chat_id = (chat_id_override or os.environ.get("TELEGRAM_CHAT_ID", "")).strip().lstrip("\ufeff\u200e\u200f\u202a\u202b\u202c")

    # Log masked debug info
    masked_token = f"{bot_token[:8]}...{bot_token[-4:]}" if len(bot_token) > 12 else "INVALID"
    print(f"DEBUG: Loaded bot_token='{masked_token}' (len={len(bot_token)}), chat_id='{chat_id}'")

    first_run_limit = int(os.environ.get("FIRST_RUN_MAX_POSTS", "3"))
    delay = float(os.environ.get("DELAY_BETWEEN_POSTS", "2.0"))

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    raw_data = json.loads(config_path.read_text(encoding="utf-8"))
    defaults = raw_data.get("defaults", {})
    sources_raw = raw_data.get("sources", [])

    sources = [
        SourceConfig(
            id=s["id"],
            source_type=s.get("type", "telegram_web"),
            name=s["name"],
            username=s.get("username", s["id"]),
            url=s["url"],
            category=s.get("category", "أخبار عامة"),
            hashtag=s.get("hashtag", "أخبار"),
            language=s.get("language", "ar"),
            enabled=s.get("enabled", True),
            max_posts=s.get("max_posts", defaults.get("max_posts_per_fetch", 15)),
        )
        for s in sources_raw
        if s.get("enabled", True)
    ]

    return AppConfig(
        telegram_bot_token=bot_token,
        telegram_chat_id=chat_id,
        first_run_max_posts=first_run_limit,
        delay_between_posts=delay,
        request_timeout=defaults.get("request_timeout_seconds", 20),
        user_agent=defaults.get("user_agent", "IraqWorldNewsBot/1.0"),
        sources=sources,
    )
