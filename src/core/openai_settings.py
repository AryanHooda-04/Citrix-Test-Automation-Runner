from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ENV_VAR = "OPENAI_API_KEY"
SECRET_FILE_ENV = "CITRIX_RUNNER_OPENAI_KEY_FILE"


@dataclass(frozen=True)
class OpenAIKeyStatus:
    configured: bool
    source: str
    detail: str
    path: Path | None = None


def openai_secret_path() -> Path:
    override = os.environ.get(SECRET_FILE_ENV, "").strip()
    if override:
        return Path(override).expanduser()

    appdata = os.environ.get("APPDATA", "").strip()
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "CitrixTestAutomationRunner" / "openai_settings.json"


def load_openai_api_key(settings: dict[str, Any] | None = None) -> str:
    settings = settings or {}
    env_name = str(settings.get("api_key_env_var") or DEFAULT_ENV_VAR)
    env_key = os.environ.get(env_name, "").strip()
    if env_key:
        return env_key

    stored_key = _read_saved_key()
    if stored_key:
        return stored_key

    config_key = str(settings.get("api_key") or "").strip()
    return config_key


def get_openai_key_status(settings: dict[str, Any] | None = None) -> OpenAIKeyStatus:
    settings = settings or {}
    env_name = str(settings.get("api_key_env_var") or DEFAULT_ENV_VAR)
    if os.environ.get(env_name, "").strip():
        return OpenAIKeyStatus(True, "environment", f"{env_name} is set for this process.")

    secret_path = openai_secret_path()
    if _read_saved_key():
        return OpenAIKeyStatus(True, "local", f"Saved local key is configured.", secret_path)

    if str(settings.get("api_key") or "").strip():
        return OpenAIKeyStatus(True, "config", "Config file API key is configured.")

    return OpenAIKeyStatus(False, "missing", f"{env_name} is not set and no local key is saved.", secret_path)


def save_openai_api_key(api_key: str) -> Path:
    cleaned = api_key.strip()
    if not cleaned:
        raise ValueError("API key cannot be empty.")

    secret_path = openai_secret_path()
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "openai_api_key": cleaned,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    secret_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return secret_path


def clear_saved_openai_api_key() -> Path:
    secret_path = openai_secret_path()
    if not secret_path.exists():
        return secret_path

    try:
        payload = json.loads(secret_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        secret_path.unlink(missing_ok=True)
        return secret_path

    if isinstance(payload, dict):
        payload.pop("openai_api_key", None)
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        if any(key for key in payload.keys() if key != "updated_at"):
            secret_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        else:
            secret_path.unlink(missing_ok=True)
    else:
        secret_path.unlink(missing_ok=True)
    return secret_path


def _read_saved_key() -> str:
    secret_path = openai_secret_path()
    try:
        payload = json.loads(secret_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("openai_api_key") or "").strip()
