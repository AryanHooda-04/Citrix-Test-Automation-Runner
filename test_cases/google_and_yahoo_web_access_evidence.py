try:
    import pyautogui
except ImportError:
    pyautogui = None


TEST_CASE = {
    "id": "TC_007_GOOGLE_YAHOO_WEB",
    "name": "Google_and_Yahoo_Web_Access_Evidence",
    "description": "Opens Google and Yahoo in Microsoft Edge and captures web access evidence.",
    "capture_screenshot": False,
}


WEBSITES = ["Web: google.com", "Web: yahoo.com"]

GOOGLE_CONSENT_PANEL_REGION = (360, 220, 1080, 520)
GOOGLE_CONSENT_OVERLAY_REGION = (40, 360, 180, 300)
YAHOO_CONSENT_PANEL_REGION = (390, 285, 1080, 500)
YAHOO_CONSENT_OVERLAY_REGION = (60, 360, 190, 300)
GOOGLE_CHROME_PROMPT_BLUE_REGION = (1580, 805, 240, 90)
GOOGLE_DONT_USE_CHROME_X = 1435
GOOGLE_DONT_USE_CHROME_Y = 845


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    ctx.step(f"Websites opened: {', '.join(WEBSITES)}")

    ctx.step(f"Step 1: Activate Citrix desktop using user input: {desktop_name}")
    ctx.activate_window_by_title(
        desktop_name,
        exact=False,
        wait_after_sec=ctx.config.wait("citrix_activation_wait_sec", 4.0),
    )

    ctx.step("Step 2: Ensure Citrix input focus with a center-screen click")
    ctx.click_screen_center(wait_after_sec=ctx.config.wait("citrix_focus_click_wait_sec", 1.0))

    ctx.step("Google Website Steps")
    _open_website_from_windows_search(ctx, "Step 3: Open Windows Search", "Step 4: Launch Google Website", "Web: google.com")

    ctx.step("Step 5: Dismiss possible Google restore previous pages prompt")
    _dismiss_possible_restore_prompt(ctx)

    ctx.step("Step 6: Conditionally accept Google terms using keyboard navigation")
    if _is_consent_modal_visible(ctx, "google"):
        ctx.step("Google consent popup detected. Running Tab x4 + Enter.")
        _accept_using_tabs(ctx, presses=4)
    else:
        ctx.step("Google consent popup not detected. Skipping Tab/Enter consent handling.")

    ctx.step("Dismiss possible Google Chrome promotion popup only if it is visible")
    _dismiss_google_chrome_prompt_if_visible(ctx)

    ctx.step("Step 7: Capture Google evidence")
    google_path = ctx.capture_evidence("google_evidence")
    ctx.step(f"Google evidence file generated: {google_path}")

    ctx.step("Yahoo Website Steps")
    _open_website_from_windows_search(ctx, "Step 8: Open Windows Search Again", "Step 9: Launch Yahoo Website", "Web: yahoo.com")

    ctx.step("Step 10: Conditionally accept Yahoo terms using keyboard navigation")
    if _is_consent_modal_visible(ctx, "yahoo"):
        ctx.step("Yahoo consent popup detected. Running Tab x10 + Enter.")
        _accept_using_tabs(ctx, presses=10)
        ctx.step("Wait after Yahoo permission handling so the page can finish loading")
        ctx.wait(ctx.config.wait("yahoo_after_permission_wait_sec", 5.0))
    else:
        ctx.step("Yahoo consent popup not detected. Skipping Tab/Enter consent handling.")

    ctx.step("Final Yahoo page settle wait before evidence capture")
    ctx.wait(ctx.config.wait("yahoo_before_capture_wait_sec", 10.0))

    ctx.step("Step 11: Capture Yahoo evidence")
    yahoo_path = ctx.capture_evidence("yahoo_evidence")
    ctx.step(f"Yahoo evidence file generated: {yahoo_path}")


def _open_website_from_windows_search(ctx, search_step, launch_step, website):
    ctx.step(search_step)
    ctx.hotkey("winleft", "s")
    ctx.wait(ctx.config.wait("windows_search_wait_sec", 2.0))

    ctx.step(launch_step)
    ctx.type_text(website, interval=0.15)
    ctx.wait(ctx.config.wait("windows_search_results_wait_sec", 5.0))
    ctx.press("enter")
    ctx.wait(ctx.config.wait("web_page_load_wait_sec", 5.0))


def _dismiss_possible_restore_prompt(ctx):
    ctx.press_repeated(
        "esc",
        presses=2,
        interval_sec=ctx.config.wait("web_restore_prompt_dismiss_wait_sec", 1.0),
    )
    ctx.wait(ctx.config.wait("web_restore_prompt_dismiss_wait_sec", 1.0))


def _accept_using_tabs(ctx, presses):
    ctx.press_repeated(
        "tab",
        presses=presses,
        interval_sec=ctx.config.wait("web_consent_tab_interval_sec", 0.3),
    )
    ctx.press("enter")
    ctx.wait(ctx.config.wait("web_consent_after_enter_wait_sec", 2.0))


def _is_consent_modal_visible(ctx, site):
    if pyautogui is None:
        raise RuntimeError("PyAutoGUI is required. Install dependencies with: pip install -r requirements.txt")

    detection_config = ctx.config.raw.get("web_consent_detection", {})
    if site == "google":
        panel_region = tuple(detection_config.get("google_panel_region", GOOGLE_CONSENT_PANEL_REGION))
        overlay_region = tuple(detection_config.get("google_overlay_region", GOOGLE_CONSENT_OVERLAY_REGION))
    elif site == "yahoo":
        panel_region = tuple(detection_config.get("yahoo_panel_region", YAHOO_CONSENT_PANEL_REGION))
        overlay_region = tuple(detection_config.get("yahoo_overlay_region", YAHOO_CONSENT_OVERLAY_REGION))
    else:
        raise ValueError(f"Unsupported consent detection site: {site}")

    ctx.check_stop()
    panel_brightness = _average_brightness(ctx.screenshot_region(panel_region))
    ctx.check_stop()
    overlay_brightness = _average_brightness(ctx.screenshot_region(overlay_region))
    delta = panel_brightness - overlay_brightness
    ctx.step(
        f"{site.title()} consent visual check: "
        f"panel={panel_brightness:.1f}, overlay={overlay_brightness:.1f}, delta={delta:.1f}"
    )

    panel_min = float(detection_config.get("panel_brightness_min", 215.0))
    overlay_max = float(detection_config.get("overlay_brightness_max", 175.0))
    delta_min = float(detection_config.get("panel_overlay_delta_min", 55.0))
    return panel_brightness >= panel_min and overlay_brightness <= overlay_max and delta >= delta_min


def _dismiss_google_chrome_prompt_if_visible(ctx):
    if pyautogui is None:
        raise RuntimeError("PyAutoGUI is required. Install dependencies with: pip install -r requirements.txt")

    config = ctx.config.raw.get("web_evidence", {})
    blue_region = tuple(config.get("google_chrome_prompt_blue_region", GOOGLE_CHROME_PROMPT_BLUE_REGION))
    click_config = config.get("google_dont_use_chrome_click", {})
    click_x = int(click_config.get("x", GOOGLE_DONT_USE_CHROME_X))
    click_y = int(click_config.get("y", GOOGLE_DONT_USE_CHROME_Y))

    ctx.check_stop()
    screenshot = ctx.screenshot_region(blue_region)
    ctx.check_stop()
    blue_ratio = _blue_pixel_ratio(screenshot)
    ctx.step(f"Google Chrome promotion blue-pixel check: {blue_ratio:.3f}")
    threshold = float(config.get("google_chrome_prompt_blue_ratio_min", 0.08))
    if blue_ratio >= threshold:
        ctx.step(f"Google Chrome promotion detected. Clicking Don't use Chrome at ({click_x}, {click_y}).")
        ctx.click(
            click_x,
            click_y,
            wait_after_sec=ctx.config.wait("web_optional_popup_dismiss_wait_sec", 1.0),
        )
        ctx.wait(ctx.config.wait("google_after_popup_handling_wait_sec", 3.0))
    else:
        ctx.step("Google Chrome promotion not detected. Skipping dismissal click.")


def _average_brightness(image):
    pixels = image.convert("RGB").getdata()
    total = 0
    count = 0
    for red, green, blue in pixels:
        total += (red + green + blue) / 3
        count += 1
    return total / count if count else 0.0


def _blue_pixel_ratio(image):
    blue_pixels = 0
    total_pixels = image.width * image.height
    for red, green, blue in image.convert("RGB").getdata():
        if blue >= 145 and 70 <= green <= 180 and red <= 90:
            blue_pixels += 1
    return blue_pixels / total_pixels if total_pixels else 0.0
