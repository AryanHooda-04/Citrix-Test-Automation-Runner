from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from core.windows_ocr import WindowsOCRUnavailable, extract_text_from_image


ZSCALER_STATUS_REGION = (950, 300, 360, 50)
ZSCALER_HEALTH_REGION = (950, 300, 400, 130)
ZSCALER_RED_RATIO_MIN = 0.003
ZSCALER_TURN_ON_WAIT_SECONDS = 15.0


def recover_zscaler_connection_if_needed(ctx: Any) -> bool:
    """Press Tab x4 and Enter when ZCCVDI opens in OFF / CONNECTION ERROR state."""
    if not zscaler_problem_state_visible(ctx):
        ctx.step("Zscaler OFF / CONNECTION ERROR state not detected. Continuing to evidence capture.")
        return False

    ctx.step("Zscaler OFF / CONNECTION ERROR detected. Press Tab x4, then Enter to trigger Turn ON.")
    for index in range(4):
        ctx.press("tab")
        ctx.step(f"Zscaler Turn ON navigation: Tab {index + 1} of 4")
        ctx.wait(0.5)
    ctx.press("enter")
    ctx.step(f"Wait {ZSCALER_TURN_ON_WAIT_SECONDS} second(s) for Zscaler to sync after Turn ON.")
    ctx.wait(ZSCALER_TURN_ON_WAIT_SECONDS)
    return True


def zscaler_problem_state_visible(ctx: Any) -> bool:
    ctx.step("Check Zscaler service status strip for OFF / CONNECTION ERROR")
    ctx.check_stop()
    image = ctx.screenshot_region(ZSCALER_STATUS_REGION)
    ctx.check_stop()

    red_ratio = _red_pixel_ratio(image)
    text = _extract_region_text(image)
    compact_text = re.sub(r"[^a-z]+", "", text.casefold())
    if not _zscaler_window_text_visible(compact_text):
        full_text = _extract_full_screen_text(ctx)
        full_compact_text = re.sub(r"[^a-z]+", "", full_text.casefold())
        if _zscaler_window_text_visible(full_compact_text):
            text = full_text
            compact_text = full_compact_text
            red_ratio = 0.0
    text_problem = _has_zscaler_problem_status(text)
    red_problem = red_ratio >= ZSCALER_RED_RATIO_MIN
    problem_visible = text_problem or red_problem

    ctx.step(
        "Zscaler status strip check: "
        f"red_ratio={red_ratio:.4f}, text='{_short_log_text(text)}', "
        f"problem_detected={problem_visible}"
    )
    return problem_visible


def zscaler_healthy_state_visible(ctx: Any) -> bool:
    ctx.step("Check Zscaler status region for Service Status ON")
    ctx.check_stop()
    image = ctx.screenshot_region(ZSCALER_HEALTH_REGION)
    ctx.check_stop()

    text = _extract_region_text(image)
    compact_text = re.sub(r"[^a-z]+", "", text.casefold())
    if not _zscaler_window_text_visible(compact_text):
        full_text = _extract_full_screen_text(ctx)
        full_compact_text = re.sub(r"[^a-z]+", "", full_text.casefold())
        if _zscaler_window_text_visible(full_compact_text):
            text = full_text
            compact_text = full_compact_text
    service_on = _has_service_on_status(text)
    authenticated = "authenticated" in compact_text
    problem_text = _has_zscaler_problem_status(text)
    healthy_visible = service_on and not problem_text

    ctx.step(
        "Zscaler healthy-state check: "
        f"text='{_short_log_text(text)}', service_on={service_on}, "
        f"authenticated={authenticated}, healthy_detected={healthy_visible}"
    )
    return healthy_visible


def _has_service_on_status(text: str) -> bool:
    normalized = (text or "").casefold()
    without_action_text = re.sub(r"\bturn\s+on\b", " ", normalized, flags=re.IGNORECASE)
    if re.search(r"\b[o0]n\b", without_action_text, re.IGNORECASE):
        return True
    compact_text = re.sub(r"[^a-z0-9]+", "", without_action_text)
    return "servicestatuson" in compact_text


def _has_zscaler_problem_status(text: str) -> bool:
    """Detect only Zscaler status failures, not unrelated words like Office."""
    normalized = (text or "").casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", normalized)
    if "connectionerror" in compact_text:
        return True

    lines = [line.strip().casefold() for line in (text or "").splitlines() if line.strip()]
    for index, line in enumerate(lines):
        line_compact = re.sub(r"[^a-z0-9]+", "", line)
        if "servicestatus" not in line_compact:
            continue
        status_window = " ".join(lines[index : index + 3])
        status_compact = re.sub(r"[^a-z0-9]+", "", status_window)
        if re.search(r"\b[o0]ff\b", status_window, re.IGNORECASE):
            return True
        if "servicestatusoff" in status_compact:
            return True
    return False


def _red_pixel_ratio(image: Any) -> float:
    red_pixels = 0
    total_pixels = image.width * image.height
    for red, green, blue in image.convert("RGB").getdata():
        if red >= 170 and green <= 105 and blue <= 105 and red - max(green, blue) >= 55:
            red_pixels += 1
    return red_pixels / total_pixels if total_pixels else 0.0


def _extract_region_text(image: Any) -> str:
    temp_path = _save_temp_region(image, "zscaler_status_")
    try:
        ocr_text = extract_text_from_image(temp_path)
    except (OSError, RuntimeError, WindowsOCRUnavailable):
        return ""
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass

    lines = [line.strip() for line in ocr_text.lines if line.strip()]
    return "\n".join(lines) or ocr_text.text.strip()


def _extract_full_screen_text(ctx: Any) -> str:
    ctx.step("Zscaler status region was inconclusive. Scanning full screenshot for Zscaler status text.")
    ctx.check_stop()
    viewport = getattr(ctx, "config", None).raw.get("citrix_viewport", {}) if getattr(ctx, "config", None) else {}
    width = int(viewport.get("coordinate_reference_width", 1920))
    height = int(viewport.get("coordinate_reference_height", 1080))
    image = ctx.screenshot_region((0, 0, width, height))
    ctx.check_stop()
    return _extract_region_text(image)


def _zscaler_window_text_visible(compact_text: str) -> bool:
    return "zscaler" in compact_text or "clientconnector" in compact_text or "connectivity" in compact_text


def _save_temp_region(image: Any, prefix: str) -> Path:
    fd, temp_name = tempfile.mkstemp(prefix=prefix, suffix=".png")
    os.close(fd)
    temp_path = Path(temp_name)
    image.save(temp_path)
    return temp_path


def _short_log_text(text: str) -> str:
    return " ".join(text.split())[:120]
