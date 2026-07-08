from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


SERVICE_NAME = "citrix-validator-bridge"
DEFAULT_OPENAI_ENDPOINT = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_MAX_IMAGE_BYTES = 8 * 1024 * 1024


class ImagePayload(BaseModel):
    filename: str = "screenshot.png"
    mime_type: str | None = None
    data_base64: str = Field(..., min_length=1)


class ValidationRequest(BaseModel):
    validation_type: str = "generic"
    evidence_label: str = "screenshot evidence"
    description: str = ""
    prompt: str = ""
    image: ImagePayload
    options: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class BridgeSettings:
    openai_api_key: str
    validator_token: str
    openai_endpoint: str
    default_model: str
    timeout_sec: float
    max_image_bytes: int


app = FastAPI(
    title="Citrix Validator Bridge",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
)


@app.get("/health")
def health(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    settings = _settings()
    _require_token(settings, authorization, allow_missing_for_health=True)
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "auth_required": bool(settings.validator_token),
        "openai_key_configured": bool(settings.openai_api_key),
    }


@app.get("/test-openai")
def test_openai(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    settings = _settings()
    _require_token(settings, authorization)
    if not settings.openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured on the validator bridge.")

    raw_text = _call_openai_smoke_test(settings)
    return {
        "ok": True,
        "service": SERVICE_NAME,
        "openai_reachable": True,
        "model": settings.default_model,
        "endpoint": settings.openai_endpoint,
        "response": raw_text[:80],
    }


@app.post("/validate")
def validate(request: ValidationRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    settings = _settings()
    _require_token(settings, authorization)
    if not settings.openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured on the validator bridge.")

    image_bytes = _decode_image(request.image.data_base64)
    if len(image_bytes) > settings.max_image_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Image is too large. Limit is {settings.max_image_bytes} bytes.",
        )

    prompt = request.prompt.strip() or _default_prompt(request)
    openai_response = _call_openai(settings, request, prompt, image_bytes)
    raw_text = _extract_output_text(openai_response)
    if not raw_text:
        return {
            "valid": False,
            "reason": "OpenAI returned no validation text.",
            "raw_text": "",
        }

    try:
        parsed = _parse_json_object(raw_text)
    except ValueError as exc:
        return {
            "valid": False,
            "reason": f"Unable to parse OpenAI validation JSON: {exc}",
            "raw_text": raw_text,
        }

    return _normalize_validation_result(parsed, raw_text=raw_text)


def _settings() -> BridgeSettings:
    max_image_bytes = _int_env("MAX_IMAGE_BYTES", DEFAULT_MAX_IMAGE_BYTES)
    return BridgeSettings(
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        validator_token=os.environ.get("VALIDATOR_TOKEN", "").strip(),
        openai_endpoint=os.environ.get("OPENAI_ENDPOINT", DEFAULT_OPENAI_ENDPOINT).strip() or DEFAULT_OPENAI_ENDPOINT,
        default_model=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        timeout_sec=float(os.environ.get("OPENAI_TIMEOUT_SEC", "90") or "90"),
        max_image_bytes=max_image_bytes,
    )


def _require_token(
    settings: BridgeSettings,
    authorization: str | None,
    *,
    allow_missing_for_health: bool = False,
) -> None:
    if not settings.validator_token:
        return
    if allow_missing_for_health and not authorization:
        return
    expected = f"Bearer {settings.validator_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized validator bridge request.")


def _decode_image(data_base64: str) -> bytes:
    try:
        return base64.b64decode(data_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 image data: {exc}") from exc


def _call_openai(
    settings: BridgeSettings,
    request: ValidationRequest,
    prompt: str,
    image_bytes: bytes,
) -> dict[str, Any]:
    mime_type = request.image.mime_type or mimetypes.guess_type(request.image.filename)[0] or "image/png"
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    options = request.options if isinstance(request.options, dict) else {}
    body = {
        "model": str(options.get("model") or settings.default_model),
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": data_url,
                        "detail": str(options.get("image_detail") or "low"),
                    },
                ],
            }
        ],
        "max_output_tokens": int(options.get("max_output_tokens") or 220),
    }
    http_request = urllib.request.Request(
        settings.openai_endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"{SERVICE_NAME}/1.0",
        },
        method="POST",
    )
    max_attempts = max(1, _int_env("OPENAI_RETRY_ATTEMPTS", 3))
    response_body = ""
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(http_request, timeout=settings.timeout_sec) as response:
                response_body = response.read().decode("utf-8", errors="replace")
                break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            if attempt < max_attempts and _is_retryable_openai_error(exc.code, detail):
                time.sleep(min(3.0, 0.75 * attempt))
                continue
            raise HTTPException(status_code=502, detail=detail or f"OpenAI returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            if attempt < max_attempts:
                time.sleep(min(3.0, 0.75 * attempt))
                continue
            raise HTTPException(status_code=502, detail=f"OpenAI request failed: {exc.reason}") from exc

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="OpenAI response was not a JSON object.")
    if parsed.get("error"):
        error_detail = parsed["error"]
        if _is_retryable_openai_error(500, json.dumps(error_detail)):
            raise HTTPException(status_code=502, detail=error_detail)
        raise HTTPException(status_code=502, detail=error_detail)
    return parsed


def _call_openai_smoke_test(settings: BridgeSettings) -> str:
    body = {
        "model": settings.default_model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Reply with exactly OK.",
                    }
                ],
            }
        ],
        "max_output_tokens": 16,
    }
    http_request = urllib.request.Request(
        settings.openai_endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"{SERVICE_NAME}/openai-smoke-test",
        },
        method="POST",
    )
    max_attempts = max(1, _int_env("OPENAI_RETRY_ATTEMPTS", 3))
    response_body = ""
    for attempt in range(1, max_attempts + 1):
        try:
            with urllib.request.urlopen(http_request, timeout=settings.timeout_sec) as response:
                response_body = response.read().decode("utf-8", errors="replace")
                break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            if attempt < max_attempts and _is_retryable_openai_error(exc.code, detail):
                time.sleep(min(3.0, 0.75 * attempt))
                continue
            raise HTTPException(status_code=502, detail=detail or f"OpenAI returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            if attempt < max_attempts:
                time.sleep(min(3.0, 0.75 * attempt))
                continue
            raise HTTPException(status_code=502, detail=f"OpenAI request failed: {exc.reason}") from exc

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail=f"OpenAI returned invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="OpenAI response was not a JSON object.")
    if parsed.get("error"):
        raise HTTPException(status_code=502, detail=parsed["error"])
    output_text = _extract_output_text(parsed)
    if not output_text:
        raise HTTPException(status_code=502, detail="OpenAI returned no smoke-test text.")
    return output_text


def _is_retryable_openai_error(status_code: int, detail: str) -> bool:
    detail_lower = detail.casefold()
    return (
        status_code in {408, 409, 429, 500, 502, 503, 504}
        or "server_error" in detail_lower
        or "temporarily unavailable" in detail_lower
        or "timeout" in detail_lower
    )


def _default_prompt(request: ValidationRequest) -> str:
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
        f"Evidence label: {request.evidence_label}\n"
        f"Validation requirement: {request.description}\n\n"
        "Validate only what is visible in the screenshot."
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


def _normalize_validation_result(payload: dict[str, Any], *, raw_text: str) -> dict[str, Any]:
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    return {
        "valid": bool(payload.get("valid")),
        "reason": str(payload.get("reason") or "").strip() or "No reason provided.",
        "cmd_hostname": str(payload.get("cmd_hostname") or "").strip(),
        "overlay_hostname": str(payload.get("overlay_hostname") or "").strip(),
        "ipv4_addresses": _string_list(payload.get("ipv4_addresses")),
        "version": str(payload.get("version") or "").strip(),
        "available": _coerce_optional_bool(payload.get("available")),
        "fields": {str(key).strip(): str(value).strip() for key, value in fields.items() if str(key).strip()},
        "raw_text": raw_text,
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    if not re.fullmatch(r"\d+", value):
        return default
    return int(value)
