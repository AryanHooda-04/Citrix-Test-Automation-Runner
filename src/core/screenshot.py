from __future__ import annotations

import io
import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from statistics import median
from threading import Event
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, ImageGrab

from core.execution_log import safe_filename
from core.stop_control import StopRequested, interruptible_sleep, wait_if_paused


class ScreenshotManager:
    def __init__(
        self,
        screenshots_dir: Path,
        settle_seconds: float,
        stop_event: Event | None = None,
        pause_event: Event | None = None,
        desktop_name: str | None = None,
        capture_region: tuple[int, int, int, int] | None = None,
        suppress_local_notifications: bool = True,
        notification_guard_wait_seconds: float = 0.8,
    ) -> None:
        self.screenshots_dir = screenshots_dir
        self.settle_seconds = settle_seconds
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.desktop_name = desktop_name
        self.capture_region = capture_region
        self.suppress_local_notifications = suppress_local_notifications
        self.notification_guard_wait_seconds = notification_guard_wait_seconds
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    def capture(self, test_case_name: str, status: str) -> Path:
        if self.settle_seconds > 0:
            interruptible_sleep(self.settle_seconds, self.stop_event, self.pause_event)
        if self.stop_event is not None and self.stop_event.is_set():
            raise StopRequested()
        wait_if_paused(self.pause_event, self.stop_event)
        if self.suppress_local_notifications:
            dismissed_count = _dismiss_overlapping_notification_windows(self.capture_region)
            if dismissed_count and self.notification_guard_wait_seconds > 0:
                interruptible_sleep(
                    self.notification_guard_wait_seconds,
                    self.stop_event,
                    self.pause_event,
                )
        wait_if_paused(self.pause_event, self.stop_event)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_filename(test_case_name)}_{status}_{timestamp}.png"
        path = self.screenshots_dir / filename
        image = self._grab_image()
        image = self._with_context_overlay(image, test_case_name)
        image.save(path)
        return path

    def _grab_image(self) -> Image.Image:
        if self.capture_region is None:
            return ImageGrab.grab()
        left, top, width, height = self.capture_region
        bbox = (left, top, left + width, top + height)
        try:
            return ImageGrab.grab(bbox=bbox, all_screens=True)
        except TypeError:
            return ImageGrab.grab(bbox=bbox)

    def copy_to_clipboard(self, image_path: Path) -> None:
        image = Image.open(image_path).convert("RGB")
        output = io.BytesIO()
        image.save(output, "BMP")
        data = output.getvalue()[14:]
        output.close()

        try:
            import win32clipboard
        except ImportError as exc:
            raise RuntimeError(
                "pywin32 is required to copy screenshots to the Windows clipboard."
            ) from exc

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
        finally:
            win32clipboard.CloseClipboard()

    def _with_context_overlay(self, image: Image.Image, test_case_name: str) -> Image.Image:
        silo_name = _silo_name_from_desktop(self.desktop_name)
        hostname = self._hostname_for_overlay(test_case_name, image)
        return _apply_overlay(image, f"Silo: {silo_name}", f"Hostname: {hostname}")

    def _hostname_for_overlay(self, test_case_name: str, image: Image.Image) -> str:
        context = _load_context(self.screenshots_dir)
        silo_name = _silo_name_from_desktop(self.desktop_name)

        if _is_hostname_capture_candidate(test_case_name):
            extracted_hostname = _normalize_hostname_for_silo(
                _extract_hostname_from_fixed_crop(image, silo_name),
                silo_name,
            )
            if extracted_hostname:
                stored_hostname = str(context.get("hostname") or "").strip()
                normalized_stored_hostname = _normalize_hostname_for_silo(stored_hostname, silo_name)
                context["hostname"] = extracted_hostname
                _save_context(self.screenshots_dir, context)
                if extracted_hostname != normalized_stored_hostname:
                    _refresh_existing_screenshot_overlays(
                        self.screenshots_dir,
                        silo_name,
                        extracted_hostname,
                    )
                return extracted_hostname

            if context.get("hostname"):
                context.pop("hostname", None)
                _save_context(self.screenshots_dir, context)
            return "Unknown"

        stored_hostname = str(context.get("hostname") or "").strip()
        hostname = _normalize_hostname_for_silo(stored_hostname, silo_name)
        if hostname != stored_hostname:
            if hostname:
                context["hostname"] = hostname
            else:
                context.pop("hostname", None)
            _save_context(self.screenshots_dir, context)

        return hostname or "Unknown"


def apply_hostname_overlay_override(
    screenshot_path: Path,
    screenshots_dir: Path,
    desktop_name: str | None,
    hostname: str,
) -> str:
    silo_name = _silo_name_from_desktop(desktop_name)
    normalized_hostname = _normalize_hostname_for_silo(hostname, silo_name)
    if not normalized_hostname:
        return ""

    context = _load_context(screenshots_dir)
    stored_hostname = str(context.get("hostname") or "").strip()
    normalized_stored_hostname = _normalize_hostname_for_silo(stored_hostname, silo_name)
    context["hostname"] = normalized_hostname
    _save_context(screenshots_dir, context)
    if normalized_hostname != normalized_stored_hostname:
        _refresh_existing_screenshot_overlays(screenshots_dir, silo_name, normalized_hostname)

    if screenshot_path.exists():
        try:
            image = Image.open(screenshot_path)
            image.load()
            updated = _apply_overlay(
                image,
                f"Silo: {silo_name}",
                f"Hostname: {normalized_hostname}",
                cover_existing=True,
            )
            updated.save(screenshot_path)
        except OSError:
            pass
    return normalized_hostname


def _apply_overlay(
    image: Image.Image,
    line_1: str,
    line_2: str,
    cover_existing: bool = False,
) -> Image.Image:
    base = image.convert("RGBA")
    draw_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(draw_layer)

    width, height = base.size
    font_size = max(16, min(24, width // 95))
    try:
        font = ImageFont.truetype("arialbd.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    lines = [line_1, line_2]
    bboxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
    text_width = max(box[2] - box[0] for box in bboxes)
    line_heights = [box[3] - box[1] for box in bboxes]
    padding_x = 14
    padding_y = 10
    gap = 4
    margin = 18
    rect_width = text_width + (padding_x * 2)
    if cover_existing:
        rect_width = min(width - (margin * 2), max(rect_width, 760))
    rect_height = sum(line_heights) + gap + (padding_y * 2)
    taskbar_clearance = max(82, height // 13)
    left = max(margin, width - rect_width - margin)
    top = max(margin, height - rect_height - taskbar_clearance)
    right = left + rect_width
    bottom = top + rect_height

    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=6,
        fill=(0, 0, 0, 255 if cover_existing else 178),
    )
    y = top + padding_y
    for line, line_height in zip(lines, line_heights):
        draw.text((left + padding_x, y), line, font=font, fill=(245, 247, 250, 255))
        y += line_height + gap

    return Image.alpha_composite(base, draw_layer).convert("RGB")


def _refresh_existing_screenshot_overlays(
    screenshots_dir: Path,
    silo_name: str,
    hostname: str,
) -> int:
    if not hostname:
        return 0

    screenshots_root = _screenshots_root(screenshots_dir)
    if not screenshots_root.exists():
        return 0

    updated_count = 0
    for path in screenshots_root.rglob("*.png"):
        try:
            image = Image.open(path)
            image.load()
            updated = _apply_overlay(
                image,
                f"Silo: {silo_name}",
                f"Hostname: {hostname}",
                cover_existing=True,
            )
            updated.save(path)
            updated_count += 1
        except OSError:
            continue

    return updated_count


def _screenshots_root(screenshots_dir: Path) -> Path:
    if screenshots_dir.parent.name.casefold() == "screenshots":
        return screenshots_dir.parent
    if screenshots_dir.name.casefold() == "screenshots":
        return screenshots_dir
    return _context_path(screenshots_dir).parent / "screenshots"


def _silo_name_from_desktop(desktop_name: str | None) -> str:
    value = (desktop_name or "").strip()
    if not value:
        return "Unknown"
    marker = " - Desktop Viewer"
    if marker.casefold() in value.casefold():
        index = value.casefold().find(marker.casefold())
        return value[:index].strip() or value
    return value


def _context_path(screenshots_dir: Path) -> Path:
    if screenshots_dir.parent.name.casefold() == "screenshots":
        evidence_root = screenshots_dir.parent.parent
    elif screenshots_dir.name.casefold() == "screenshots":
        evidence_root = screenshots_dir.parent
    else:
        evidence_root = screenshots_dir.parent
    return evidence_root / "screenshot_context.json"


def _load_context(screenshots_dir: Path) -> dict:
    path = _context_path(screenshots_dir)
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_context(screenshots_dir: Path, context: dict) -> None:
    path = _context_path(screenshots_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            json.dump(context, file, indent=2)
    except OSError:
        return


def _is_hostname_capture_candidate(test_case_name: str) -> bool:
    return safe_filename(test_case_name) in {
        "Hostname_and_IP_Evidence",
        "Hostname_Validation",
    }


def _extract_hostname_from_fixed_crop(image: Image.Image, silo_name: str = "") -> str:
    if image.width < 240 or image.height < 160:
        return ""

    crop = image.crop((1, 170, min(image.width, 360), min(image.height, 205))).convert("L")
    text = _ocr_terminal_hostname_crop(crop)
    text = _normalize_hostname_for_silo(text, silo_name)
    if _looks_like_terminal_hostname(text):
        return text

    hostname = _extract_hostname_from_terminal_lines(image, silo_name)
    if hostname:
        return hostname
    return ""


def _extract_hostname_from_terminal_lines(image: Image.Image, silo_name: str = "") -> str:
    scan_width = min(image.width, 900)
    scan_height = min(image.height, 260)
    scan = image.crop((0, 40, scan_width, scan_height)).convert("L")
    mask = _image_to_mask(scan)
    if not mask:
        return ""

    row_counts = [sum(1 for value in row if value) for row in mask]
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for y, count in enumerate(row_counts):
        if count > 3 and start is None:
            start = y
        elif count <= 3 and start is not None:
            if y - start >= 6:
                bands.append((start, y - 1))
            start = None
    if start is not None and len(row_counts) - start >= 6:
        bands.append((start, len(row_counts) - 1))

    candidates = []
    for top, bottom in bands:
        crop_top = max(0, top - 4)
        crop_bottom = min(scan.height, bottom + 5)
        crop = scan.crop((0, crop_top, min(scan.width, 700), crop_bottom)).convert("L")
        text = _normalize_hostname_for_silo(_ocr_terminal_hostname_crop(crop).strip(), silo_name)
        if _looks_like_terminal_hostname(text):
            candidates.append(text)

    if candidates:
        return max(candidates, key=_hostname_candidate_score)
    return ""


def _looks_like_terminal_hostname(value: str) -> bool:
    if not _looks_like_hostname(value):
        return False
    if not _looks_like_citrix_hostname(value):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{2,62}", value))


def _looks_like_citrix_hostname(value: str, silo_name: str = "") -> bool:
    normalized = (value or "").strip().upper()
    if not _looks_like_windows_hostname(normalized):
        return False
    if "-" not in normalized and not re.search(r"RW\d{2}", normalized):
        return False
    silo_number = _silo_number_from_name(silo_name)
    if silo_number and f"RW{silo_number}-" not in normalized:
        return False
    return bool(
        re.search(r"RW\d{2}-[A-Z0-9]", normalized)
        or normalized.startswith(("VTA", "VTE", "VTN", "VPD", "SILO"))
    )


def _looks_like_windows_hostname(value: str) -> bool:
    normalized = (value or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9](?:[A-Z0-9-]{1,61}[A-Z0-9])?", normalized):
        return False
    if len(normalized) < 3 or len(normalized) > 63:
        return False
    if normalized.isdigit():
        return False
    if not any(char.isdigit() for char in normalized) and "-" not in normalized:
        return False
    blocked_words = {
        "HOSTNAME",
        "IPCONFIG",
        "WINDOWS",
        "CONFIGURATION",
        "UNKNOWN",
    }
    return normalized not in blocked_words


def _hostname_candidate_score(value: str) -> tuple[int, int, int]:
    normalized = value.upper()
    prefix_score = 1 if normalized.startswith(("VTA", "VTE", "VTN", "VPD", "SILO")) else 0
    hyphen_score = normalized.count("-")
    rw_score = 1 if re.search(r"RW\d{2}-", normalized) else 0
    return (rw_score, prefix_score, hyphen_score)


def _should_replace_hostname(current: str, candidate: str) -> bool:
    if not candidate:
        return False
    if not current:
        return True
    return _hostname_candidate_score(candidate) >= _hostname_candidate_score(current)


def _normalize_hostname_for_silo(value: str, silo_name: str = "") -> str:
    raw_value = _normalize_hostname_separators(value).strip().upper()
    if not raw_value:
        return ""

    tokens = re.findall(r"[A-Z0-9][A-Z0-9-]{1,62}", raw_value)
    if not tokens:
        compact = re.sub(r"[^A-Z0-9-]", "", raw_value)
        tokens = [compact] if compact else []

    normalized_tokens: list[str] = []
    for token in tokens:
        candidate = _normalize_hostname_token(token)
        if _looks_like_citrix_hostname(candidate, silo_name):
            normalized_tokens.append(candidate)

    if normalized_tokens:
        return max(normalized_tokens, key=_hostname_candidate_score)

    candidate = _normalize_hostname_token(re.sub(r"[^A-Z0-9-]", "", raw_value))
    silo_number = _silo_number_from_name(silo_name)
    candidate = re.sub(r"^YFLA(\d+)RW", r"VTA\1RW", candidate)

    if _looks_like_citrix_hostname(candidate, silo_name):
        return candidate

    embedded_match = re.search(r"(?P<host>[A-Z0-9]{3,12}RW\d{2}-[A-Z0-9][A-Z0-9-]{1,31})", candidate)
    if embedded_match:
        embedded = _normalize_hostname_token(embedded_match.group("host"))
        if _looks_like_citrix_hostname(embedded, silo_name):
            return embedded

    suffix_match = re.search(r"RW(?P<silo>\d{2})-(?P<tail>[A-Z0-9][A-Z0-9-]{1,31})", candidate)
    if suffix_match:
        matched_silo = suffix_match.group("silo")
        tail = suffix_match.group("tail")
        if not silo_number or matched_silo == silo_number:
            prefix = _hostname_prefix_for_silo(matched_silo, silo_name)
            normalized = f"{prefix}RW{matched_silo}-{tail}"
            if _looks_like_citrix_hostname(normalized, silo_name):
                return normalized

    return ""


def _normalize_hostname_token(value: str) -> str:
    candidate = _normalize_hostname_separators(value).strip().upper()
    candidate = re.sub(r"[^A-Z0-9-]", "", candidate)
    if not candidate:
        return ""
    candidate = re.sub(r"^YFLA(\d+)RW", r"VTA\1RW", candidate)
    return re.sub(r"^([A-Z]{3})I(?=RW\d{2}-)", r"\g<1>1", candidate)


def _normalize_hostname_separators(value: str) -> str:
    return (
        (value or "")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
    )


def _silo_number_from_name(silo_name: str) -> str:
    match = re.search(r"SIL[O0](\d{2})", silo_name or "", re.IGNORECASE)
    return match.group(1) if match else ""


def _hostname_prefix_for_silo(silo_number: str, silo_name: str = "") -> str:
    ap_match = re.search(r"-AP(\d+)\b", silo_name or "", re.IGNORECASE)
    if ap_match:
        return f"VTA{ap_match.group(1)}"
    if silo_number == "07" or "-AP" in (silo_name or "").upper():
        return "VTA1"
    return "VTE1"


def _ocr_terminal_hostname_crop(crop: Image.Image) -> str:
    glyphs = _segment_terminal_glyphs(crop)
    if not glyphs:
        return ""
    templates = _terminal_ocr_templates()
    output = []
    for glyph in glyphs:
        normalized = _normalize_mask(glyph)
        best_char = ""
        best_score = 1.0
        for char, char_templates in templates.items():
            score = min(_mask_distance(normalized, template) for template in char_templates)
            if score < best_score:
                best_char = char
                best_score = score
        if best_char:
            output.append(best_char)
    return "".join(output)


def _segment_terminal_glyphs(crop: Image.Image) -> list[list[list[bool]]]:
    mask = _image_to_mask(crop)
    if not mask:
        return []
    height = len(mask)
    width = len(mask[0])
    column_counts = [
        sum(1 for y in range(height) if mask[y][x])
        for x in range(width)
    ]
    bands: list[tuple[int, int]] = []
    start: int | None = None
    for x, count in enumerate(column_counts):
        if count > 0 and start is None:
            start = x
        elif count == 0 and start is not None:
            if x - start >= 2:
                bands.append((start, x - 1))
            start = None
    if start is not None and width - start >= 2:
        bands.append((start, width - 1))

    glyph_bands: list[tuple[int, int]] = []
    widths = [right - left + 1 for left, right in bands if right - left + 1 > 3]
    normal_width = median(widths) if widths else 10
    for left, right in bands:
        width = right - left + 1
        if normal_width > 0 and width > normal_width * 1.75:
            part_count = max(2, round(width / normal_width))
            for index in range(part_count):
                part_left = left + round(index * width / part_count)
                part_right = left + round((index + 1) * width / part_count) - 1
                if part_right - part_left >= 1:
                    glyph_bands.append((part_left, part_right))
        else:
            glyph_bands.append((left, right))

    glyphs = []
    for left, right in glyph_bands:
        rows_with_pixels = [
            y for y in range(height)
            if any(mask[y][x] for x in range(left, right + 1))
        ]
        if not rows_with_pixels:
            continue
        top = min(rows_with_pixels)
        bottom = max(rows_with_pixels)
        glyphs.append([
            [mask[y][x] for x in range(left, right + 1)]
            for y in range(top, bottom + 1)
        ])
    return glyphs


def _image_to_mask(image: Image.Image, threshold: int = 120) -> list[list[bool]]:
    grayscale = image.convert("L")
    return [
        [grayscale.getpixel((x, y)) > threshold for x in range(grayscale.width)]
        for y in range(grayscale.height)
    ]


@lru_cache(maxsize=1)
def _terminal_ocr_templates() -> dict[str, tuple[tuple[bool, ...], ...]]:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._"
    font_paths = [
        Path(r"C:\Windows\Fonts\CascadiaMono.ttf"),
        Path(r"C:\Windows\Fonts\CascadiaCode.ttf"),
        Path(r"C:\Windows\Fonts\consola.ttf"),
        Path(r"C:\Windows\Fonts\consolab.ttf"),
    ]
    sizes = range(20, 32)
    templates: dict[str, list[tuple[bool, ...]]] = {char: [] for char in alphabet}
    for font_path in font_paths:
        if not font_path.exists():
            continue
        for size in sizes:
            try:
                font = ImageFont.truetype(str(font_path), size)
            except OSError:
                continue
            for char in alphabet:
                mask = _render_template_mask(char, font)
                if mask:
                    templates[char].append(_normalize_mask(mask))
    return {char: tuple(values) for char, values in templates.items() if values}


def _render_template_mask(char: str, font) -> list[list[bool]]:
    image = Image.new("L", (80, 80), 0)
    draw = ImageDraw.Draw(image)
    draw.text((10, 10), char, font=font, fill=255)
    mask = _image_to_mask(image, threshold=20)
    rows = [
        y for y, row in enumerate(mask)
        if any(row)
    ]
    cols = [
        x for x in range(len(mask[0]))
        if any(mask[y][x] for y in range(len(mask)))
    ]
    if not rows or not cols:
        return []
    return [
        [mask[y][x] for x in range(min(cols), max(cols) + 1)]
        for y in range(min(rows), max(rows) + 1)
    ]


def _normalize_mask(mask: list[list[bool]], size: tuple[int, int] = (24, 18)) -> tuple[bool, ...]:
    height = len(mask)
    width = len(mask[0]) if height else 0
    if not height or not width:
        return tuple(False for _ in range(size[0] * size[1]))
    image = Image.new("L", (width, height), 0)
    pixels = image.load()
    for y, row in enumerate(mask):
        for x, value in enumerate(row):
            if value:
                pixels[x, y] = 255
    resized = image.resize(size, Image.Resampling.NEAREST)
    return tuple(pixel > 128 for pixel in resized.getdata())


def _mask_distance(left: tuple[bool, ...], right: tuple[bool, ...]) -> float:
    if len(left) != len(right) or not left:
        return 1.0
    mismatches = sum(1 for left_value, right_value in zip(left, right) if left_value != right_value)
    return mismatches / len(left)


def _parse_hostname_from_console_text(text: str) -> str:
    normalized = _normalize_hostname_separators(text).replace("\r\n", "\n")
    match = re.search(r">\s*hostname\s*\n+\s*([A-Za-z0-9][A-Za-z0-9._-]{1,63})", normalized, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    lines = [line.strip() for line in normalized.splitlines()]
    for index, line in enumerate(lines):
        if line.casefold().endswith(">hostname") or line.casefold() == "hostname":
            for candidate in lines[index + 1 : index + 5]:
                if _looks_like_hostname(candidate):
                    return candidate
    if len(lines) <= 3:
        for candidate in lines:
            if _looks_like_hostname(candidate):
                return candidate
    before_ipconfig = re.split(
        r"\b(?:Windows\s+IP\s+Configuration|Ethernet\s+adapter|IPv4\s+Address)\b",
        normalized,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    match = re.search(
        r"\b[A-Za-z0-9]{2,12}RW\d{2}-[A-Za-z0-9][A-Za-z0-9._-]{1,31}\b",
        before_ipconfig,
        re.IGNORECASE,
    )
    if match:
        return match.group(0).strip()
    return ""


def _looks_like_hostname(value: str) -> bool:
    if not value or len(value) > 64:
        return False
    if " " in value or ":" in value or "\\" in value:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value))


def _dismiss_overlapping_notification_windows(capture_region: tuple[int, int, int, int] | None) -> int:
    try:
        import win32con
        import win32gui
    except ImportError:
        return 0

    target_rect = _capture_region_to_rect(capture_region)
    candidates: list[int] = []

    def collect(hwnd, _extra) -> None:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        class_name = win32gui.GetClassName(hwnd).strip()
        try:
            rect = tuple(int(value) for value in win32gui.GetWindowRect(hwnd))
        except Exception:
            return
        if not _is_notification_like_window(title, class_name, rect, target_rect):
            return
        candidates.append(hwnd)

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        return 0

    dismissed = 0
    for hwnd in candidates:
        try:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            dismissed += 1
        except Exception:
            continue
    return dismissed


def _capture_region_to_rect(capture_region: tuple[int, int, int, int] | None) -> tuple[int, int, int, int] | None:
    if capture_region is None:
        return None
    left, top, width, height = capture_region
    return int(left), int(top), int(left + width), int(top + height)


def _is_notification_like_window(
    title: str,
    class_name: str,
    rect: tuple[int, int, int, int],
    target_rect: tuple[int, int, int, int] | None,
) -> bool:
    left, top, right, bottom = rect
    width = right - left
    height = bottom - top
    if width < 140 or height < 80 or width > 900 or height > 720:
        return False
    if target_rect is not None and not _rects_overlap(rect, target_rect):
        return False

    normalized_title = title.casefold()
    normalized_class = class_name.casefold()
    title_keywords = (
        "microsoft teams",
        "teams",
        "outlook",
        "reminder",
        "notification",
        "toast",
        "incoming call",
        "is calling you",
        "zoom",
        "webex",
    )
    class_keywords = (
        "windows.ui.core.corewindow",
        "applicationframewindow",
        "xaml",
        "toast",
        "notification",
    )
    if any(keyword in normalized_title for keyword in title_keywords):
        return True
    if not title and any(keyword in normalized_class for keyword in class_keywords):
        return True
    return False


def _rects_overlap(left_rect: Iterable[int], right_rect: Iterable[int]) -> bool:
    left_a, top_a, right_a, bottom_a = tuple(left_rect)
    left_b, top_b, right_b, bottom_b = tuple(right_rect)
    return left_a < right_b and right_a > left_b and top_a < bottom_b and bottom_a > top_b
