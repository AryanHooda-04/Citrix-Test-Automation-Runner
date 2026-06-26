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
    def runtime_profile(self) -> dict[str, Any]:
        return self.raw.get("runtime_profile", {})

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

    def runtime_mode(self) -> str:
        profiles = self.runtime_profile.get("profiles", {})
        configured = str(self.runtime_profile.get("mode", "normal")).strip().lower()
        return configured if configured in profiles else "normal"

    def runtime_mode_label(self) -> str:
        mode = self.runtime_mode()
        profile = self.runtime_profile.get("profiles", {}).get(mode, {})
        return str(profile.get("label", mode.title()))

    def runtime_wait_multiplier(self) -> float:
        mode = self.runtime_mode()
        profile = self.runtime_profile.get("profiles", {}).get(mode, {})
        try:
            return float(profile.get("multiplier", 1.0))
        except (TypeError, ValueError):
            return 1.0

    def wait(self, key: str, default: float = 0.5) -> float:
        base_wait = float(self.waits.get(key, default))
        mode = self.runtime_mode()
        profile = self.runtime_profile.get("profiles", {}).get(mode, {})
        excluded_keys = set(profile.get("excluded_wait_keys", []))
        minimum_unscaled = float(profile.get("minimum_unscaled_wait_sec", 0.0))

        if key in excluded_keys or base_wait <= minimum_unscaled:
            return base_wait

        multiplier = self.runtime_wait_multiplier()
        minimum_wait = float(profile.get("minimum_wait_sec", 0.0))
        return max(minimum_wait, base_wait * multiplier)


def load_config(root_dir: Path) -> AppConfig:
    config_path = root_dir / "config" / "config.json"
    with config_path.open("r", encoding="utf-8") as file:
        raw = json.load(file)

    config = AppConfig(root_dir=root_dir, raw=raw)
    config.path("test_cases_dir").mkdir(parents=True, exist_ok=True)
    return config
