from __future__ import annotations

import base64
import json
import mimetypes
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.openai_settings import load_openai_api_key


@dataclass(frozen=True)
class AIValidationResult:
    valid: bool
    reason: str
    cmd_hostname: str = ""
    overlay_hostname: str = ""
    ipv4_addresses: tuple[str, ...] = ()
    version: str = ""
    available: bool | None = None
    fields: dict[str, str] = field(default_factory=dict)
    raw_text: str = ""


def validate_hostname_ip_evidence(
    image_path: Path,
    settings: dict[str, Any],
) -> AIValidationResult:
    if _uses_bridge(settings):
        return _validate_with_bridge(
            image_path,
            settings,
            validation_type="hostname_ip",
            evidence_label="Hostname/IP evidence screenshot",
            prompt=_hostname_ip_validation_prompt(),
        )

    api_key = load_openai_api_key(settings)
    if not api_key:
        env_name = str(settings.get("api_key_env_var") or "OPENAI_API_KEY")
        return AIValidationResult(
            valid=False,
            reason=f"{env_name} is not set and no saved OpenAI API key is configured.",
        )

    if not image_path.exists():
        return AIValidationResult(
            valid=False,
            reason=f"Screenshot does not exist: {image_path}",
        )

    try:
        response = _send_validation_request(image_path, api_key, settings)
    except Exception as exc:
        return AIValidationResult(valid=False, reason=f"OpenAI request failed: {exc}")

    if response.get("error"):
        error = response["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        return AIValidationResult(valid=False, reason=f"OpenAI returned an error: {message}")

    raw_text = _extract_output_text(response)
    if not raw_text:
        return AIValidationResult(valid=False, reason="OpenAI returned no validation text.")

    try:
        payload = _parse_json_object(raw_text)
    except ValueError as exc:
        return AIValidationResult(
            valid=False,
            reason=f"Unable to parse OpenAI validation JSON: {exc}",
            raw_text=raw_text,
        )

    return _result_from_payload(payload, raw_text=raw_text)


def validate_screenshot_evidence(
    image_path: Path,
    settings: dict[str, Any],
    *,
    description: str,
    evidence_label: str = "screenshot evidence",
) -> AIValidationResult:
    prompt = _generic_validation_prompt(description=description, evidence_label=evidence_label)
    if _uses_bridge(settings):
        return _validate_with_bridge(
            image_path,
            settings,
            validation_type="generic",
            evidence_label=evidence_label,
            description=description,
            prompt=prompt,
        )

    api_key = load_openai_api_key(settings)
    if not api_key:
        env_name = str(settings.get("api_key_env_var") or "OPENAI_API_KEY")
        return AIValidationResult(
            valid=False,
            reason=f"{env_name} is not set and no saved OpenAI API key is configured.",
        )

    if not image_path.exists():
        return AIValidationResult(
            valid=False,
            reason=f"Screenshot does not exist: {image_path}",
        )

    try:
        response = _send_validation_request(image_path, api_key, settings, prompt=prompt)
    except Exception as exc:
        return AIValidationResult(valid=False, reason=f"OpenAI request failed: {exc}")

    if response.get("error"):
        error = response["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        return AIValidationResult(valid=False, reason=f"OpenAI returned an error: {message}")

    raw_text = _extract_output_text(response)
    if not raw_text:
        return AIValidationResult(valid=False, reason="OpenAI returned no validation text.")

    try:
        payload = _parse_json_object(raw_text)
    except ValueError as exc:
        return AIValidationResult(
            valid=False,
            reason=f"Unable to parse OpenAI validation JSON: {exc}",
            raw_text=raw_text,
        )

    return _result_from_payload(payload, raw_text=raw_text)


def _uses_bridge(settings: dict[str, Any]) -> bool:
    return str(settings.get("mode") or "direct").strip().casefold() == "bridge"


def _validate_with_bridge(
    image_path: Path,
    settings: dict[str, Any],
    *,
    validation_type: str,
    evidence_label: str,
    prompt: str,
    description: str = "",
) -> AIValidationResult:
    if not image_path.exists():
        return AIValidationResult(
            valid=False,
            reason=f"Screenshot does not exist: {image_path}",
        )

    bridge_url = _bridge_validate_url(settings)
    if not bridge_url:
        return AIValidationResult(
            valid=False,
            reason="Validator bridge URL is not configured.",
        )

    try:
        response = _send_bridge_validation_request(
            image_path,
            settings,
            bridge_url=bridge_url,
            validation_type=validation_type,
            evidence_label=evidence_label,
            description=description,
            prompt=prompt,
        )
    except Exception as exc:
        return AIValidationResult(valid=False, reason=f"Validator bridge request failed: {exc}")

    if response.get("error"):
        error = response["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        return AIValidationResult(valid=False, reason=f"Validator bridge returned an error: {message}")

    payload = response.get("result") if isinstance(response.get("result"), dict) else response
    if not isinstance(payload, dict):
        return AIValidationResult(valid=False, reason="Validator bridge returned an unreadable response.")

    raw_text = str(payload.get("raw_text") or response.get("raw_text") or "").strip()
    if "valid" not in payload and raw_text:
        try:
            payload = _parse_json_object(raw_text)
        except ValueError as exc:
            return AIValidationResult(
                valid=False,
                reason=f"Unable to parse validator bridge JSON: {exc}",
                raw_text=raw_text,
            )

    return _result_from_payload(payload, raw_text=raw_text)


def _send_bridge_validation_request(
    image_path: Path,
    settings: dict[str, Any],
    *,
    bridge_url: str,
    validation_type: str,
    evidence_label: str,
    description: str,
    prompt: str,
) -> dict[str, Any]:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    body = {
        "validation_type": validation_type,
        "evidence_label": evidence_label,
        "description": description,
        "prompt": prompt,
        "image": {
            "filename": image_path.name,
            "mime_type": mime_type,
            "data_base64": image_data,
        },
        "options": {
            "model": settings.get("model", "gpt-4.1-mini"),
            "image_detail": settings.get("image_detail", "low"),
            "max_output_tokens": int(settings.get("max_output_tokens", 220)),
        },
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "CitrixTestAutomationRunner/validator-bridge",
    }
    token = _bridge_token(settings)
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        bridge_url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    timeout_sec = float(settings.get("bridge_timeout_sec", settings.get("timeout_sec", 90)))
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            response_body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response from validator bridge: {exc}") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Validator bridge response was not a JSON object.")
    return parsed


def _bridge_validate_url(settings: dict[str, Any]) -> str:
    raw_url = str(settings.get("bridge_url") or "").strip()
    if not raw_url:
        return ""
    cleaned = raw_url.rstrip("/")
    if cleaned.endswith("/validate"):
        return cleaned
    return f"{cleaned}/validate"


def _bridge_token(settings: dict[str, Any]) -> str:
    token = str(settings.get("bridge_token") or "").strip()
    if token:
        return token
    env_name = str(settings.get("bridge_token_env_var") or "CITRIX_VALIDATOR_TOKEN").strip()
    return os.environ.get(env_name, "").strip() if env_name else ""


def _send_validation_request(
    image_path: Path,
    api_key: str,
    settings: dict[str, Any],
    *,
    prompt: str | None = None,
) -> dict[str, Any]:
    payload_path = _write_payload_file(image_path, settings, prompt=prompt)
    try:
        command = [
            "curl.exe",
            "--silent",
            "--show-error",
        ]
        if bool(settings.get("curl_ssl_no_revoke", True)):
            command.append("--ssl-no-revoke")
        command.extend(["--config", "-", "--data-binary", f"@{payload_path}"])
        curl_config = "\n".join(
            [
                f'url = "{settings.get("endpoint", "https://api.openai.com/v1/responses")}"',
                'request = "POST"',
                f'header = "Authorization: Bearer {api_key}"',
                'header = "Content-Type: application/json"',
            ]
        )

        completed = subprocess.run(
            command,
            input=curl_config,
            capture_output=True,
            text=True,
            timeout=float(settings.get("timeout_sec", 90)),
            check=False,
        )
        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            stdout = (completed.stdout or "").strip()
            detail = stderr or stdout or f"curl exited with code {completed.returncode}"
            raise RuntimeError(detail[:1000])
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid JSON response from OpenAI: {exc}") from exc
    finally:
        try:
            payload_path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_payload_file(image_path: Path, settings: dict[str, Any], *, prompt: str | None = None) -> Path:
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    image_data = base64.b64encode(image_path.read_bytes()).decode("ascii")
    data_url = f"data:{mime_type};base64,{image_data}"
    body = {
        "model": settings.get("model", "gpt-4.1-mini"),
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt or _hostname_ip_validation_prompt(),
                    },
                    {
                        "type": "input_image",
                        "image_url": data_url,
                        "detail": settings.get("image_detail", "low"),
                    },
                ],
            }
        ],
        "max_output_tokens": int(settings.get("max_output_tokens", 220)),
    }

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json",
        prefix="citrix_ai_validation_",
        delete=False,
    )
    with handle:
        json.dump(body, handle)
    return Path(handle.name)


def _hostname_ip_validation_prompt() -> str:
    return (
        "You are validating a Citrix desktop test evidence screenshot.\n\n"
        "Return only valid JSON with this exact shape:\n"
        "{\n"
        '  "valid": true,\n'
        '  "cmd_hostname": "HOSTNAME_FROM_COMMAND_OUTPUT",\n'
        '  "overlay_hostname": "HOSTNAME_FROM_BOTTOM_RIGHT_OVERLAY",\n'
        '  "ipv4_addresses": ["IP_FROM_IPCONFIG"],\n'
        '  "reason": "short explanation"\n'
        "}\n\n"
        "Validation rules:\n"
        "1. The image must show Windows Command Prompt or Windows Terminal.\n"
        "2. The command prompt must show hostname evidence. Accept any of these: "
        "the command 'hostname' followed by hostname output; a literal 'hostname' label printed by echo "
        "followed by hostname output; or a standalone hostname-looking output line before Windows IP Configuration "
        "(this is valid for combined cmd /k hostname/ipconfig runs).\n"
        "3. The command prompt must show ipconfig evidence. Accept any of these: the command 'ipconfig'; "
        "a literal 'ipconfig' label printed by echo; or visible Windows IP Configuration output.\n"
        "4. The ipconfig output must include at least one real IPv4 address line. Reject loopback, 0.0.0.0, and empty values.\n"
        "5. The bottom-right screenshot overlay is expected to contain two lines: "
        "'Silo: <silo name>' and 'Hostname: <value>'. The Silo line is valid and must not cause failure.\n"
        "6. Read only the value after 'Hostname:' in the overlay. That overlay hostname value must exactly match the hostname command output.\n"
        "Do not reject the screenshot because the overlay includes a Silo line; that is required context.\n"
        "If any rule fails, set valid to false and explain the failure in reason."
    )


def _generic_validation_prompt(*, description: str, evidence_label: str) -> str:
    return (
        "You are validating a Citrix desktop test evidence screenshot.\n\n"
        "Return only valid JSON with this exact shape:\n"
        "{\n"
        '  "valid": true,\n'
        '  "available": true,\n'
        '  "version": "VERSION_IF_VISIBLE_OR_EMPTY",\n'
        '  "fields": {"optional_key": "optional_value"},\n'
        '  "reason": "short explanation"\n'
        "}\n\n"
        f"Evidence label: {evidence_label}\n"
        f"Validation requirement: {description}\n\n"
        "Rules:\n"
        "1. Validate only what is visible in the screenshot.\n"
        "2. If the required screen or evidence content is visible, set valid to true.\n"
        "3. If the screenshot shows the wrong app, wrong page, missing result, or a known bad state, set valid to false.\n"
        "4. If a software version is visible and relevant, copy it exactly into version; otherwise use an empty string.\n"
        "5. If an application/result availability is relevant, set available to true or false; otherwise set it to null.\n"
        "6. Keep reason short and specific."
    )


def _extract_output_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"].strip()
    chunks: list[str] = []
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("response did not contain a JSON object")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("response JSON was not an object")
    return parsed


def _result_from_payload(payload: dict[str, Any], *, raw_text: str = "") -> AIValidationResult:
    return AIValidationResult(
        valid=bool(payload.get("valid")),
        reason=str(payload.get("reason") or "").strip() or "No reason provided.",
        cmd_hostname=str(payload.get("cmd_hostname") or "").strip(),
        overlay_hostname=str(payload.get("overlay_hostname") or "").strip(),
        ipv4_addresses=tuple(str(value).strip() for value in payload.get("ipv4_addresses", []) if str(value).strip()),
        version=str(payload.get("version") or "").strip(),
        available=_coerce_optional_bool(payload.get("available")),
        fields=_coerce_string_dict(payload.get("fields")),
        raw_text=raw_text,
    )


def _coerce_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, str):
        lowered = value.strip().casefold()
        if lowered in {"true", "yes", "available", "present"}:
            return True
        if lowered in {"false", "no", "not available", "missing", "absent"}:
            return False
    return None


def _coerce_string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, field_value in value.items():
        key_text = str(key).strip()
        if not key_text:
            continue
        result[key_text] = str(field_value).strip()
    return result
