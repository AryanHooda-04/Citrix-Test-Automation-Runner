from __future__ import annotations

import re
import tempfile
from pathlib import Path

from core.windows_ocr import WindowsOCRUnavailable, extract_text_from_image


EDGE_SIGN_IN_REGION = (1000, 600, 450, 140)


def find_sign_in_button_target(ctx) -> tuple[int, int] | None:
    ctx.check_stop()
    detection = ctx.config.raw.get("edge_sync_detection", {})
    region = tuple(detection.get("signin_region", EDGE_SIGN_IN_REGION))
    if len(region) != 4:
        region = EDGE_SIGN_IN_REGION
    region_x, region_y, region_width, region_height = (int(value) for value in region)

    screenshot = ctx.screenshot_region(region)
    ctx.check_stop()

    sign_in_text = _extract_sign_in_region_text(ctx, screenshot)
    if sign_in_text:
        normalized_text = re.sub(r"[^a-z0-9]+", "", sign_in_text.casefold())
        text_detected = "signin" in normalized_text
        ctx.step(
            "Edge Sign in OCR text check: "
            f"detected={text_detected}; text='{_shorten_ocr_text(sign_in_text)}'"
        )
        if text_detected:
            dark_target = _find_dark_sign_in_button_target(ctx, screenshot, region_x, region_y, detection)
            if dark_target is not None:
                return dark_target

            blue_target = _find_blue_sign_in_button_target(ctx, screenshot, region_x, region_y, detection, region)
            if blue_target is not None:
                return blue_target

            fallback = detection.get("signin_fallback_click", {})
            fallback_x = int(fallback.get("x", region_x + int(region_width * 0.30)))
            fallback_y = int(fallback.get("y", region_y + int(region_height * 0.58)))
            ctx.step(
                "Edge Sign in text detected, but button pixels were inconclusive. "
                f"Using fallback click ({fallback_x}, {fallback_y})."
            )
            return (fallback_x, fallback_y)

    return _find_blue_sign_in_button_target(ctx, screenshot, region_x, region_y, detection, region)


def _extract_sign_in_region_text(ctx, screenshot) -> str:
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="edge_signin_", suffix=".png", delete=False) as temp_file:
            temp_path = Path(temp_file.name)
        screenshot.save(temp_path)
        ocr_result = extract_text_from_image(temp_path)
        return "\n".join(ocr_result.lines) or ocr_result.text
    except (WindowsOCRUnavailable, OSError, RuntimeError, ValueError) as exc:
        ctx.step(f"Edge Sign in OCR text check unavailable: {exc}")
        return ""
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _find_dark_sign_in_button_target(ctx, screenshot, region_x, region_y, detection) -> tuple[int, int] | None:
    dark_pixels = 0
    total_pixels = screenshot.width * screenshot.height
    min_x = screenshot.width
    min_y = screenshot.height
    max_x = 0
    max_y = 0
    for index, (red, green, blue) in enumerate(screenshot.convert("RGB").getdata()):
        if red <= 85 and green <= 85 and blue <= 85:
            dark_pixels += 1
            x = index % screenshot.width
            y = index // screenshot.width
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

    dark_ratio = dark_pixels / total_pixels if total_pixels else 0
    bbox_width = max_x - min_x + 1 if dark_pixels else 0
    bbox_height = max_y - min_y + 1 if dark_pixels else 0
    ctx.step(
        "Edge Sign in dark-button check: "
        f"{dark_pixels}/{total_pixels} ({dark_ratio:.3f}), bbox={bbox_width}x{bbox_height}"
    )
    min_ratio = detection.get("signin_dark_ratio_min", 0.015)
    if dark_ratio < min_ratio or bbox_width < 35 or bbox_height < 20:
        return None
    return (region_x + ((min_x + max_x) // 2), region_y + ((min_y + max_y) // 2))


def _find_blue_sign_in_button_target(ctx, screenshot, region_x, region_y, detection, region) -> tuple[int, int] | None:
    blue_pixels = 0
    total_pixels = screenshot.width * screenshot.height
    min_x = screenshot.width
    min_y = screenshot.height
    max_x = 0
    max_y = 0
    for index, (red, green, blue) in enumerate(screenshot.convert("RGB").getdata()):
        if blue >= 150 and 70 <= green <= 170 and red <= 80:
            blue_pixels += 1
            x = index % screenshot.width
            y = index // screenshot.width
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)

    blue_ratio = blue_pixels / total_pixels if total_pixels else 0
    ctx.step(
        "Edge Sign in button blue-pixel check: "
        f"{blue_pixels}/{total_pixels} ({blue_ratio:.3f}) in region {region}"
    )
    if blue_ratio < detection.get("signin_blue_ratio_min", 0.05):
        return None

    if blue_pixels:
        return (region_x + ((min_x + max_x) // 2), region_y + ((min_y + max_y) // 2))

    return None


def _shorten_ocr_text(text: str, limit: int = 120) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit - 3]}..."
