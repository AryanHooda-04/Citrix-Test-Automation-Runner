import os
import re
import tempfile
from pathlib import Path

from core.windows_ocr import WindowsOCRUnavailable, extract_text_from_image

try:
    import pyautogui
except ImportError:
    pyautogui = None


TEST_CASE = {
    "id": "TC_006_OFFICE_APPLICATIONS",
    "name": "Office_Applications_Launch",
    "description": "Launches PowerPoint, captures About dialog evidence, then closes the app.",
    "capture_screenshot": False,
}


OFFICE_APPLICATIONS = [
    ("PowerPoint", "powerpnt", "powerpoint", "powerpnt_evidence"),
]
OFFICE_POST_LAUNCH_HANDLER_ALIASES = {
    "powerpoint": ("powerpoint", "powerpnt", "ppt"),
}

DEFAULT_ABOUT_DETECTION_REGION = {
    "x": 950,
    "y": 850,
    "width": 130,
    "height": 130,
}
DEFAULT_ABOUT_FALLBACK_CLICK = {
    "x": 1015,
    "y": 680,
}
DEFAULT_PRIVACY_POPUP_BLUE_REGION = {
    "x": 1450,
    "y": 815,
    "width": 230,
    "height": 80,
}
DEFAULT_PRIVACY_POPUP_CLOSE_CLICK = {
    "x": 1555,
    "y": 848,
}
DEFAULT_LICENSE_WARNING_REGION = {
    "x": 940,
    "y": 105,
    "width": 160,
    "height": 340,
}
DEFAULT_ACTIVATE_PRODUCT_CLICK = {
    "x": 1015,
    "y": 380,
}


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    ctx.step("Office PowerPoint evidence implementation")
    ctx.step(f"Application launched: {', '.join(name for name, _, _, _ in OFFICE_APPLICATIONS)}")

    ctx.step(f"Step 1: Activate Citrix desktop using user input: {desktop_name}")
    ctx.activate_window_by_title(
        desktop_name,
        exact=False,
        wait_after_sec=ctx.config.wait("citrix_activation_wait_sec", 4.0),
    )

    ctx.step("Step 2: Ensure Citrix input focus with a center-screen click")
    ctx.click_screen_center(wait_after_sec=ctx.config.wait("citrix_focus_click_wait_sec", 1.0))

    for app_name, run_command, retry_key, evidence_name in OFFICE_APPLICATIONS:
        _launch_application(ctx, app_name, run_command)
        _handle_post_launch_dialog(ctx, retry_key)
        _capture_office_about_evidence(ctx, app_name, retry_key, evidence_name)
        _close_office_application(ctx, app_name)

    ctx.step("Office PowerPoint evidence capture completed.")


def _launch_application(ctx, app_name, command):
    ctx.step(f"Launch {app_name} using Run dialog")
    ctx.hotkey("winleft", "r")
    ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))
    ctx.type_text(command, interval=0.15)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("office_single_app_load_wait_sec", 15.0))


def _handle_post_launch_dialog(ctx, retry_key):
    office_config = ctx.config.raw.get("office_evidence", {})
    dialog_config = _post_launch_dialog_config_for_desktop(ctx, office_config, retry_key)
    if not dialog_config:
        return

    tab_presses = int(dialog_config.get("tab_presses", 1))
    ctx.step(
        f"Handle {retry_key} post-launch dialog: press Tab x{tab_presses}, then Enter"
    )
    ctx.press("tab", presses=tab_presses)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("office_post_launch_dialog_after_enter_wait_sec", 3.0))


def _capture_office_about_evidence(ctx, app_name, retry_key, evidence_name):
    office_config = ctx.config.raw.get("office_evidence", {})
    account_config = _account_click_config_for_desktop(ctx, office_config)
    account_retries = office_config.get("account_click_retries", {})
    about_config = _about_click_config_for_desktop(ctx, office_config)
    about_detection_region = _about_detection_region_for_desktop(ctx, office_config)

    ctx.step(f"Maximize {app_name} window")
    ctx.maximize_active_window()
    ctx.wait(ctx.config.wait("office_after_maximize_wait_sec", 2.0))
    _handle_office_privacy_popup(ctx, app_name, office_config)

    ctx.step(f"Open Account tab in {app_name}")
    ctx.step(
        "Office Account click coordinate selected: "
        f"({int(account_config.get('x'))}, {int(account_config.get('y'))})"
    )
    retry_count = int(account_retries.get(retry_key, 1))
    for attempt in range(retry_count):
        is_last_attempt = attempt == retry_count - 1
        wait_after_click = (
            ctx.config.wait("office_account_page_wait_sec", 4.0)
            if is_last_attempt
            else ctx.config.wait("office_account_retry_wait_sec", 1.0)
        )
        if retry_count > 1:
            ctx.step(f"Account click attempt {attempt + 1} of {retry_count} for {app_name}")
        ctx.click(
            int(account_config.get("x")),
            int(account_config.get("y")),
            wait_after_sec=wait_after_click,
        )

    _handle_office_license_warning(ctx, app_name, office_config)
    _click_about_button(ctx, app_name, about_config, office_config, about_detection_region)

    ctx.step(f"Capture {app_name} About dialog evidence")
    ctx.capture_evidence(evidence_name)

    ctx.step(f"Close About {app_name} dialog")
    ctx.hotkey("alt", "f4")
    ctx.wait(1.0)


def _close_office_application(ctx, app_name):
    ctx.step(f"Close {app_name} application")
    ctx.hotkey("alt", "f4")
    ctx.wait(1.0)
    ctx.step(f"Handle possible Save changes prompt for {app_name}")
    ctx.press("n")
    ctx.wait(ctx.config.wait("office_after_close_wait_sec", 3.0))


def _click_about_button(ctx, app_name, about_config, office_config, detection_region):
    fallback_config = office_config.get("about_fallback_click", DEFAULT_ABOUT_FALLBACK_CLICK)

    primary_x = int(about_config.get("x"))
    primary_y = int(about_config.get("y"))
    fallback_x = int(fallback_config.get("x", DEFAULT_ABOUT_FALLBACK_CLICK["x"]))
    fallback_y = int(fallback_config.get("y", DEFAULT_ABOUT_FALLBACK_CLICK["y"]))

    ctx.step(f"Check configured About {app_name} button area before clicking")
    if _about_button_structure_visible(ctx, detection_region):
        ctx.step(f"Configured About {app_name} button detected. Clicking ({primary_x}, {primary_y}).")
        click_x = primary_x
        click_y = primary_y
    else:
        ctx.step(
            f"Configured About {app_name} button not detected. "
            f"Using fallback coordinates ({fallback_x}, {fallback_y})."
        )
        click_x = fallback_x
        click_y = fallback_y

    ctx.step(f"Open About {app_name}")
    about_wait = ctx.config.wait("office_about_dialog_wait_sec", 4.0)
    ctx.click(click_x, click_y, wait_after_sec=about_wait)
    if _about_dialog_visible(ctx, app_name):
        return

    if (click_x, click_y) != (fallback_x, fallback_y):
        ctx.step(
            f"About {app_name} dialog was not detected after primary click. "
            f"Retrying fallback coordinates ({fallback_x}, {fallback_y})."
        )
        ctx.click(fallback_x, fallback_y, wait_after_sec=about_wait)
        if _about_dialog_visible(ctx, app_name):
            return

    raise RuntimeError(
        f"About {app_name} dialog did not open; mandatory Session ID was not visible."
    )


def _handle_office_license_warning(ctx, app_name, office_config):
    license_config = office_config.get("license_warning", {})
    if not bool(license_config.get("enabled", True)):
        return

    region_config = _desktop_override_config(
        ctx,
        office_config,
        "license_warning_region_overrides",
        license_config.get("region", DEFAULT_LICENSE_WARNING_REGION),
    )
    click_config = _desktop_override_config(
        ctx,
        office_config,
        "activate_product_click_overrides",
        license_config.get("activate_product_click", DEFAULT_ACTIVATE_PRODUCT_CLICK),
    )

    if not _activate_product_text_visible(ctx, region_config):
        ctx.step(f'No "Activate Product" text detected for {app_name}; continuing directly to About button.')
        return

    click_x = int(click_config.get("x", DEFAULT_ACTIVATE_PRODUCT_CLICK["x"]))
    click_y = int(click_config.get("y", DEFAULT_ACTIVATE_PRODUCT_CLICK["y"]))
    wait_after_activation = float(
        license_config.get(
            "activation_wait_sec",
            ctx.config.wait("office_activate_product_wait_sec", 15.0),
        )
    )
    ctx.step(
        f'Office "Activate Product" text detected for {app_name}. '
        f"Click Activate Product at ({click_x}, {click_y})."
    )
    ctx.click(click_x, click_y, wait_after_sec=0.5)
    ctx.step(f"Wait {wait_after_activation} second(s) for Office license activation to update.")
    ctx.wait(wait_after_activation)


def _activate_product_text_visible(ctx, region_config):
    region = (
        int(region_config.get("x", DEFAULT_LICENSE_WARNING_REGION["x"])),
        int(region_config.get("y", DEFAULT_LICENSE_WARNING_REGION["y"])),
        int(region_config.get("width", DEFAULT_LICENSE_WARNING_REGION["width"])),
        int(region_config.get("height", DEFAULT_LICENSE_WARNING_REGION["height"])),
    )

    ctx.check_stop()
    screenshot = ctx.screenshot_region(region)
    ctx.check_stop()

    text = _extract_region_text(screenshot)
    compact_text = re.sub(r"[^a-z0-9]+", "", text.casefold())
    detected = "activateproduct" in compact_text or (
        "activate" in compact_text and "product" in compact_text
    )
    ctx.step(
        'Office "Activate Product" OCR check: '
        f"text='{_short_log_text(text)}', detected={detected}"
    )
    return detected


def _extract_region_text(image):
    temp_path = _save_temp_region(image, "office_activate_")
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


def _save_temp_region(image, prefix):
    fd, temp_name = tempfile.mkstemp(prefix=prefix, suffix=".png")
    os.close(fd)
    temp_path = Path(temp_name)
    image.save(temp_path)
    return temp_path


def _short_log_text(text):
    return " ".join(text.split())[:120]


def _about_dialog_visible(ctx, app_name):
    viewport = ctx.config.raw.get("citrix_viewport", {})
    width = int(viewport.get("coordinate_reference_width", 1920))
    height = int(viewport.get("coordinate_reference_height", 1080))

    ctx.check_stop()
    screenshot = ctx.screenshot_region((0, 0, width, height))
    ctx.check_stop()

    text = _extract_region_text(screenshot)
    text_lower = text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)
    product_token = re.sub(r"[^a-z0-9]+", "", app_name.casefold())
    dialog_visible = (
        "aboutmicrosoft" in compact_text
        and product_token in compact_text
        and _session_id_visible(text)
    )
    ctx.step(
        f"Office About dialog verification: "
        f"text='{_short_log_text(text)}', detected={dialog_visible}"
    )
    return dialog_visible


def _session_id_visible(text):
    for line in (text or "").splitlines():
        if not re.search(r"\bsession\s*(?:i?d|1d|d)?\b", line, re.IGNORECASE):
            continue
        if re.search(r"[A-Za-z0-9][A-Za-z0-9._-]{7,}", line):
            return True
    return False


def _about_button_structure_visible(ctx, region_config):
    if pyautogui is None:
        raise RuntimeError("PyAutoGUI is required. Install dependencies with: pip install -r requirements.txt")

    region = (
        int(region_config.get("x", DEFAULT_ABOUT_DETECTION_REGION["x"])),
        int(region_config.get("y", DEFAULT_ABOUT_DETECTION_REGION["y"])),
        int(region_config.get("width", DEFAULT_ABOUT_DETECTION_REGION["width"])),
        int(region_config.get("height", DEFAULT_ABOUT_DETECTION_REGION["height"])),
    )
    threshold = float(
        ctx.config.raw.get("office_evidence", {}).get("about_detection_non_blank_ratio_min", 0.025)
    )

    ctx.check_stop()
    screenshot = ctx.screenshot_region(region)
    ctx.check_stop()

    non_blank_pixels = 0
    total_pixels = screenshot.width * screenshot.height
    for red, green, blue in screenshot.convert("RGB").getdata():
        brightness = (red + green + blue) / 3
        contrast = max(red, green, blue) - min(red, green, blue)
        if brightness < 210 or contrast > 35:
            non_blank_pixels += 1

    ratio = non_blank_pixels / total_pixels if total_pixels else 0.0
    ctx.step(f"Office About button visual check: {non_blank_pixels}/{total_pixels} ({ratio:.3f})")
    return ratio >= threshold


def _account_click_config_for_desktop(ctx, office_config):
    default_config = office_config.get("account_click", {})
    return _click_config_for_desktop(ctx, office_config, "account_click_overrides", default_config)


def _about_click_config_for_desktop(ctx, office_config):
    default_config = office_config.get("about_click", {})
    return _click_config_for_desktop(ctx, office_config, "about_click_overrides", default_config)


def _about_detection_region_for_desktop(ctx, office_config):
    default_config = office_config.get("about_detection_region", DEFAULT_ABOUT_DETECTION_REGION)
    return _desktop_override_config(
        ctx,
        office_config,
        "about_detection_region_overrides",
        default_config,
    )


def _click_config_for_desktop(ctx, office_config, override_key, default_config):
    config = _desktop_override_config(ctx, office_config, override_key, default_config)
    return {
        "x": int(config.get("x", default_config.get("x", 0))),
        "y": int(config.get("y", default_config.get("y", 0))),
    }


def _desktop_override_config(ctx, office_config, override_key, default_config):
    desktop_name = (ctx.citrix_desktop_name or "").casefold()
    overrides = office_config.get(override_key, {})
    if isinstance(overrides, dict):
        for marker, override in overrides.items():
            if str(marker).casefold() in desktop_name and isinstance(override, dict):
                merged_config = dict(default_config)
                merged_config.update(override)
                return merged_config
    return default_config


def _post_launch_dialog_config_for_desktop(ctx, office_config, retry_key):
    desktop_name = (ctx.citrix_desktop_name or "").casefold()
    handlers = office_config.get("post_launch_dialog_handlers", {})
    if not isinstance(handlers, dict):
        return None
    handler_keys = OFFICE_POST_LAUNCH_HANDLER_ALIASES.get(retry_key, (retry_key,))
    for marker, app_handlers in handlers.items():
        if str(marker).casefold() not in desktop_name or not isinstance(app_handlers, dict):
            continue
        for handler_key in handler_keys:
            handler = app_handlers.get(handler_key)
            if isinstance(handler, dict):
                return handler if bool(handler.get("enabled", True)) else None
    return None


def _handle_office_privacy_popup(ctx, app_name, office_config):
    privacy_config = office_config.get("privacy_popup", {})
    if not bool(privacy_config.get("enabled", True)):
        return

    region_config = privacy_config.get("blue_button_region", DEFAULT_PRIVACY_POPUP_BLUE_REGION)
    close_config = privacy_config.get("close_click", DEFAULT_PRIVACY_POPUP_CLOSE_CLICK)
    threshold = float(privacy_config.get("blue_ratio_min", 0.08))

    if not _office_privacy_close_button_visible(ctx, region_config, threshold):
        if _office_privacy_text_visible(ctx):
            ctx.step(f"Office privacy popup detected for {app_name} by OCR fallback.")
        else:
            ctx.step(f"No Office privacy popup detected for {app_name}")
            return
    else:
        ctx.step(f"Office privacy popup detected for {app_name} by visual button check.")

    close_x = int(close_config.get("x", DEFAULT_PRIVACY_POPUP_CLOSE_CLICK["x"]))
    close_y = int(close_config.get("y", DEFAULT_PRIVACY_POPUP_CLOSE_CLICK["y"]))
    ctx.step(f"Click Office privacy popup Close at ({close_x}, {close_y}).")
    ctx.click(
        close_x,
        close_y,
        wait_after_sec=ctx.config.wait("office_privacy_popup_after_close_wait_sec", 2.0),
    )


def _office_privacy_text_visible(ctx):
    viewport = ctx.config.raw.get("citrix_viewport", {})
    width = int(viewport.get("coordinate_reference_width", 1920))
    height = int(viewport.get("coordinate_reference_height", 1080))

    ctx.check_stop()
    screenshot = ctx.screenshot_region((0, 0, width, height))
    ctx.check_stop()

    text = _extract_region_text(screenshot)
    compact_text = re.sub(r"[^a-z0-9]+", "", text.casefold())
    detected = (
        "yourprivacymatters" in compact_text
        or "thanksforusingoffice" in compact_text
        or ("privacymatters" in compact_text and "office" in compact_text)
    )
    ctx.step(
        "Office privacy popup OCR check: "
        f"text='{_short_log_text(text)}', detected={detected}"
    )
    return detected


def _office_privacy_close_button_visible(ctx, region_config, threshold):
    region = (
        int(region_config.get("x", DEFAULT_PRIVACY_POPUP_BLUE_REGION["x"])),
        int(region_config.get("y", DEFAULT_PRIVACY_POPUP_BLUE_REGION["y"])),
        int(region_config.get("width", DEFAULT_PRIVACY_POPUP_BLUE_REGION["width"])),
        int(region_config.get("height", DEFAULT_PRIVACY_POPUP_BLUE_REGION["height"])),
    )
    ctx.check_stop()
    screenshot = ctx.screenshot_region(region)
    ctx.check_stop()

    blue_pixels = 0
    total_pixels = screenshot.width * screenshot.height
    for red, green, blue in screenshot.convert("RGB").getdata():
        if blue > 120 and 45 <= green <= 140 and red < 80 and blue - red > 70 and blue - green > 25:
            blue_pixels += 1

    ratio = blue_pixels / total_pixels if total_pixels else 0.0
    ctx.step(f"Office privacy popup blue button check: {blue_pixels}/{total_pixels} ({ratio:.3f})")
    return ratio >= threshold
