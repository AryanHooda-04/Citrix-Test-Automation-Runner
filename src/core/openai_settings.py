from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
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


@dataclass(frozen=True)
class OpenAIKeyTestResult:
    ok: bool
    message: str


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
    if _uses_validator_bridge(settings):
        bridge_url = _bridge_base_url(settings)
        if bridge_url:
            return OpenAIKeyStatus(True, "bridge", "Validator bridge is configured; local OpenAI key is not required.")
        return OpenAIKeyStatus(False, "missing", "Validator bridge mode is enabled, but bridge_url is not configured.")

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


def test_openai_api_key(settings: dict[str, Any] | None = None, api_key: str | None = None) -> OpenAIKeyTestResult:
    settings = settings or {}
    if _uses_validator_bridge(settings):
        return _test_validator_bridge(settings)

    cleaned = (api_key or "").strip() or load_openai_api_key(settings)
    if not cleaned:
        env_name = str(settings.get("api_key_env_var") or DEFAULT_ENV_VAR)
        return OpenAIKeyTestResult(False, f"{env_name} is not set and no saved local key is configured.")

    endpoint = str(settings.get("test_endpoint") or "https://api.openai.com/v1/models")
    command = [
        "curl.exe",
        "--silent",
        "--show-error",
    ]
    if bool(settings.get("curl_ssl_no_revoke", True)):
        command.append("--ssl-no-revoke")
    command.extend(["--config", "-"])
    curl_config = "\n".join(
        [
            f'url = "{endpoint}"',
            'request = "GET"',
            f'header = "Authorization: Bearer {cleaned}"',
        ]
    )

    try:
        completed = subprocess.run(
            command,
            input=curl_config,
            capture_output=True,
            text=True,
            timeout=float(settings.get("test_timeout_sec", 20)),
            check=False,
        )
    except Exception as exc:
        return OpenAIKeyTestResult(False, f"Could not test key: {exc}")

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or f"curl exited with code {completed.returncode}").strip()
        return OpenAIKeyTestResult(False, detail[:300])

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return OpenAIKeyTestResult(False, "OpenAI returned an unreadable response.")

    if isinstance(payload, dict) and payload.get("error"):
        error = payload["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        return OpenAIKeyTestResult(False, str(message)[:300])

    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return OpenAIKeyTestResult(True, "Key test passed. OpenAI API responded successfully.")

    return OpenAIKeyTestResult(False, "OpenAI response did not include the expected models list.")


def _uses_validator_bridge(settings: dict[str, Any]) -> bool:
    return str(settings.get("mode") or "direct").strip().casefold() == "bridge"


def _bridge_base_url(settings: dict[str, Any]) -> str:
    return str(settings.get("bridge_url") or "").strip().rstrip("/")


def _bridge_token(settings: dict[str, Any]) -> str:
    token = str(settings.get("bridge_token") or "").strip()
    if token:
        return token
    env_name = str(settings.get("bridge_token_env_var") or "CITRIX_VALIDATOR_TOKEN").strip()
    return os.environ.get(env_name, "").strip() if env_name else ""


def _test_validator_bridge(settings: dict[str, Any]) -> OpenAIKeyTestResult:
    base_url = _bridge_base_url(settings)
    if not base_url:
        return OpenAIKeyTestResult(False, "Validator bridge URL is not configured.")

    test_path = str(settings.get("bridge_test_path") or "/test-openai").strip() or "/test-openai"
    if not test_path.startswith("/"):
        test_path = f"/{test_path}"
    endpoint = f"{base_url}{test_path}"
    headers = {"User-Agent": "CitrixTestAutomationRunner/validator-bridge-test"}
    token = _bridge_token(settings)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(endpoint, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=float(settings.get("test_timeout_sec", 20))) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if exc.code == 404:
            return OpenAIKeyTestResult(
                False,
                "Validator bridge does not have /test-openai yet. Restart or redeploy the updated bridge.",
            )
        return OpenAIKeyTestResult(False, (_bridge_error_message(detail) or f"Validator bridge returned HTTP {exc.code}")[:300])
    except urllib.error.URLError as exc:
        return OpenAIKeyTestResult(False, f"Validator bridge test failed: {exc.reason}"[:300])
    except Exception as exc:
        return OpenAIKeyTestResult(False, f"Validator bridge test failed: {exc}"[:300])

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return OpenAIKeyTestResult(False, "Validator bridge returned an unreadable response.")

    if isinstance(payload, dict) and payload.get("ok") and payload.get("openai_reachable"):
        return OpenAIKeyTestResult(True, "Validator bridge and OpenAI API responded successfully.")
    if isinstance(payload, dict) and payload.get("ok"):
        return OpenAIKeyTestResult(False, "Validator bridge responded, but OpenAI API reachability was not confirmed.")
    return OpenAIKeyTestResult(False, "Validator bridge OpenAI test did not report ok=true.")


def _bridge_error_message(detail: str) -> str:
    if not detail:
        return ""
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return detail
    if not isinstance(payload, dict):
        return detail
    value = payload.get("detail") or payload.get("error")
    if isinstance(value, dict):
        message = value.get("message") or value.get("detail") or json.dumps(value)
        return str(message)
    if value:
        return str(value)
    return detail


def _read_saved_key() -> str:
    secret_path = openai_secret_path()
    try:
        payload = json.loads(secret_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("openai_api_key") or "").strip()
