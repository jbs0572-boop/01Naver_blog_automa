from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import final

from tools.contract_types import ContractError, JSONMap, JSONValue
from tools.runner_state import atomic_write_json


@final
class DashboardNotifications:

    def __init__(self, root: Path) -> None:
        self.path: Path = root / ".automation/dashboard/notifications.json"
        self._items: list[JSONMap] = []
        if self.path.exists():
            value: JSONValue = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ContractError("dashboard notifications are invalid")
            items = value.get("items")
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                raise ContractError("dashboard notifications are invalid")
            parsed: list[JSONMap] = []
            for item in items:
                if isinstance(item, dict):
                    parsed.append(item)
            self._items = parsed

    def record(self, source_id: str, status: str, attempt_id: str, message: str) -> JSONMap:
        key = f"{source_id}\0{status}\0{attempt_id}"
        notice_id = hashlib.sha256(key.encode()).hexdigest()[:24]
        existing = next((item for item in self._items if item.get("id") == notice_id), None)
        if existing is not None:
            return existing
        item: JSONMap = {
            "id": notice_id,
            "source_id": source_id,
            "status": status,
            "attempt_id": attempt_id,
            "message": message,
            "created_at": datetime.now(UTC).isoformat(),
            "read_at": None,
        }
        self._items.append(item)
        self._save()
        return item

    def mark_read(self, notice_id: str) -> JSONMap:
        item = next((entry for entry in self._items if entry.get("id") == notice_id), None)
        if item is None:
            raise ContractError("dashboard notification not found")
        if item.get("read_at") is None:
            item["read_at"] = datetime.now(UTC).isoformat()
            self._save()
        return item

    def view(self) -> JSONMap:
        items = list(reversed(self._items[-100:]))
        values: list[JSONValue] = list(items)
        return {
            "items": values,
            "unread_count": sum(item.get("read_at") is None for item in items),
        }

    def _save(self) -> None:
        values: list[JSONValue] = list(self._items)
        atomic_write_json(self.path, {"items": values})


__all__ = ["DashboardNotifications"]
