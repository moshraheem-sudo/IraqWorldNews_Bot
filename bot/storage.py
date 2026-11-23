"""Deduplication and persistent state storage for Iraq & World News Bot."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

logger = logging.getLogger(__name__)

MAX_HISTORY_ENTRIES = 5000


class StorageManager:
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.data: Dict[str, Any] = self._load()
        self.sent_ids: Set[str] = set(self.data.get("sent_ids", []))
        self.is_first_run: bool = len(self.sent_ids) == 0

    def _load(self) -> Dict[str, Any]:
        if not self.file_path.exists():
            return {
                "schema_version": "1.0",
                "last_updated": None,
                "total_sent": 0,
                "sent_ids": [],
            }
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not read sent_posts file (%s), initializing empty state: %s", self.file_path, e)
            return {
                "schema_version": "1.0",
                "last_updated": None,
                "total_sent": 0,
                "sent_ids": [],
            }

    def is_sent(self, post_id: str) -> bool:
        return post_id in self.sent_ids

    def mark_sent(self, post_id: str) -> None:
        if post_id not in self.sent_ids:
            self.sent_ids.add(post_id)
            sent_list = self.data.get("sent_ids", [])
            sent_list.append(post_id)
            # Keep history trimmed to avoid bloating
            if len(sent_list) > MAX_HISTORY_ENTRIES:
                sent_list = sent_list[-MAX_HISTORY_ENTRIES:]
            self.data["sent_ids"] = sent_list
            self.data["total_sent"] = int(self.data.get("total_sent", 0)) + 1

    def save(self) -> None:
        self.data["last_updated"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.file_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.file_path)
        logger.info("Saved deduplication state to %s (tracked IDs: %d)", self.file_path, len(self.sent_ids))
