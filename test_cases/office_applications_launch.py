try:
    import pyautogui
except ImportError:
    pyautogui = None


TEST_CASE = {
    "id": "TC_006_OFFICE_APPLICATIONS",
    "name": "Office_Applications_Launch",
    "description": "Launches each Office app, captures About dialog evidence, then closes the app.",
    "capture_screenshot": False,
}


OFFICE_APPLICATIONS = [
    ("Word", "winword", "word", "word_evidence"),
    ("PowerPoint", "powerpnt", "powerpoint", "powerpnt_evidence"),
    ("Excel", "excel", "excel", "excel_evidence"),
]

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


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    ctx.step("Office app-by-app evidence implementation")
    ctx.step(f"Applications launched: {', '.join(name for name, _, _, _ in OFFICE_APPLICATIONS)}")

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
        _capture_office_about_evidence(ctx, app_name, retry_key, evidence_name)
        _close_office_application(ctx, app_name)

    ctx.step("Office application evidence capture completed.")


def _launch_application(ctx, app_name, command):
    ctx.step(f"Launch {app_name} using Run dialog")
    ctx.hotkey("winleft", "r")
    ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))
    ctx.type_text(command, interval=0.15)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("office_single_app_load_wait_sec", 15.0))


def _capture_office_about_evidence(ctx, app_name, retry_key, evidence_name):
    office_config = ctx.config.raw.get("office_evidence", {})
    account_config = office_config.get("account_click", {})
    account_retries = office_config.get("account_click_retries", {})
    about_config = office_config.get("about_click", {})

    ctx.step(f"Maximize {app_name} window")
    ctx.maximize_active_window()
    ctx.wait(ctx.config.wait("office_after_maximize_wait_sec", 2.0))

    ctx.step(f"Open Account tab in {app_name}")
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

    _click_about_button(ctx, app_name, about_config, office_config)

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


def _click_about_button(ctx, app_name, about_config, office_config):
    detection_region = office_config.get("about_detection_region", DEFAULT_ABOUT_DETECTION_REGION)
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
    ctx.click(
        click_x,
        click_y,
        wait_after_sec=ctx.config.wait("office_about_dialog_wait_sec", 4.0),
    )


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
