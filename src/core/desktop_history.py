from __future__ import annotations

import json
from pathlib import Path

from core.config import AppConfig


class DesktopNameHistory:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        settings = config.raw.get("desktop_history", {})
        self.max_items = int(settings.get("max_items", 5))
        self.path = config.resolve_path(settings.get("file", "config/desktop_history.json"))

    def load(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError):
            return []
        items = payload.get("desktop_names", [])
        if not isinstance(items, list):
            return []
        return [str(item).strip() for item in items if str(item).strip()][: self.max_items]

    def add(self, desktop_name: str) -> list[str]:
        cleaned = desktop_name.strip()
        if not cleaned:
            return self.load()

        items = [item for item in self.load() if item.casefold() != cleaned.casefold()]
        items.insert(0, cleaned)
        items = items[: self.max_items]

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as file:
            json.dump({"desktop_names": items}, file, indent=2)
        return items
