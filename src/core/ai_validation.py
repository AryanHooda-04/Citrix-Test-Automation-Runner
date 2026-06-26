from __future__ import annotations

import base64
import json
import mimetypes
import subprocess
import tempfile
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


def validate_screenshot_evidence(
    image_path: Path,
    settings: dict[str, Any],
    *,
    description: str,
    evidence_label: str = "screenshot evidence",
) -> AIValidationResult:
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

    prompt = _generic_validation_prompt(description=description, evidence_label=evidence_label)
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

    return AIValidationResult(
        valid=bool(payload.get("valid")),
        reason=str(payload.get("reason") or "").strip() or "No reason provided.",
        version=str(payload.get("version") or "").strip(),
        available=_coerce_optional_bool(payload.get("available")),
        fields=_coerce_string_dict(payload.get("fields")),
        raw_text=raw_text,
    )


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
