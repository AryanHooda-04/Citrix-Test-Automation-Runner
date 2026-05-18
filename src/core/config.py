from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AppConfig:
    root_dir: Path
    raw: dict[str, Any]

    @property
    def waits(self) -> dict[str, float]:
        return self.raw.get("waits", {})

    @property
    def screenshot_settings(self) -> dict[str, bool]:
        return self.raw.get("screenshots", {})

    @property
    def pyautogui_settings(self) -> dict[str, Any]:
        return self.raw.get("pyautogui", {})

    def path(self, key: str) -> Path:
        value = self.raw.get("paths", {}).get(key)
        if not value:
            raise KeyError(f"Missing path configuration: {key}")
        return self.resolve_path(value)

    def resolve_path(self, value: str) -> Path:
        expanded = os.path.expandvars(value).replace("\\", "/")
        path = Path(expanded).expanduser()
        if not path.is_absolute():
            path = self.root_dir / path
        return path

    def wait(self, key: str, default: float = 0.5) -> float:
        return float(self.waits.get(key, default))


def load_config(root_dir: Path) -> AppConfig:
    config_path = root_dir / "config" / "config.json"
    with config_path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    config = AppConfig(root_dir=root_dir, raw=raw)
    config.path("test_cases_dir").mkdir(parents=True, exist_ok=True)
    return config
