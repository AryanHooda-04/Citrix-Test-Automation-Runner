try:
    import pyautogui
except ImportError:
    pyautogui = None


TEST_CASE = {
    "id": "TC_010_SHAKEDOWN_EDGE_SYNC",
    "name": "Shakedown_Edge_Sync_Evidence",
    "description": "Captures Edge sync readiness and browser version evidence from Edge settings.",
    "capture_screenshot": False,
}


EDGE_SEARCH_TEXT = "Apps: Microsoft Edge"
EDGE_SIGN_IN_FALLBACK_X = 1210
EDGE_SIGN_IN_FALLBACK_Y = 618
EDGE_SIGN_IN_REGION = (1080, 585, 300, 70)


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    edge_opened = False
    try:
        ctx.step(f"Step 1: Activate Citrix desktop using user input: {desktop_name}")
        ctx.activate_window_by_title(
            desktop_name,
            exact=False,
            wait_after_sec=ctx.config.wait("citrix_activation_wait_sec", 4.0),
        )

        ctx.step("Step 2: Ensure Citrix input focus with a center-screen click")
        ctx.click_screen_center(wait_after_sec=ctx.config.wait("citrix_focus_click_wait_sec", 1.0))

        ctx.step("Step 3: Open Microsoft Edge from Windows Search")
        ctx.hotkey("winleft", "s")
        ctx.wait(ctx.config.wait("windows_search_wait_sec", 2.0))
        ctx.type_text(EDGE_SEARCH_TEXT, interval=0.15)
        ctx.wait(ctx.config.wait("edge_search_results_wait_sec", 10.0))
        ctx.press("enter")
        ctx.wait(ctx.config.wait("edge_launch_wait_sec", 10.0))
        edge_opened = True

        ctx.step("Step 4: Maximize Edge window")
        ctx.maximize_active_window()

        ctx.step("Step 5: Open Edge Settings sync view with Alt + E, then G")
        ctx.hotkey("alt", "e")
        ctx.press("g")
        ctx.wait(ctx.config.wait("edge_settings_page_wait_sec", 10.0))

        ctx.step("Step 6: Check whether Edge profile Sign in button is visible")
        sign_in_target = _find_sign_in_button_target(ctx)
        if sign_in_target is not None:
            sign_in_x, sign_in_y = sign_in_target
            ctx.step(
                "Edge Sign in button detected. Click Sign in "
                f"at detected coordinates ({sign_in_x}, {sign_in_y})"
            )
            ctx.click(
                sign_in_x,
                sign_in_y,
                wait_after_sec=ctx.config.wait("edge_profile_signin_wait_sec", 15.0),
            )
        else:
            ctx.step("Edge profile already appears signed in. Skipping Sign in click.")

        ctx.step("Step 7: Capture Edge sync evidence screenshot and copy it to clipboard")
        sync_path = ctx.capture_evidence("edge_sync")
        ctx.step(f"Edge sync evidence file generated: {sync_path}")

        ctx.step("Step 8: Open Edge About/version page with Alt + E, then B, then M")
        ctx.hotkey("alt", "e")
        ctx.press("b")
        ctx.wait(1.0)
        ctx.press("m")
        ctx.wait(ctx.config.wait("edge_about_page_wait_sec", 5.0))

        ctx.step("Step 9: Capture Edge browser version evidence screenshot and copy it to clipboard")
        version_path = ctx.capture_evidence("edge_browser_version")
        ctx.step(f"Edge browser version evidence file generated: {version_path}")

    finally:
        if edge_opened:
            ctx.step("Step 10: Close Microsoft Edge with Alt + F4")
            ctx.hotkey("alt", "f4")
            ctx.wait(ctx.config.wait("edge_close_wait_sec", 2.0))


def _find_sign_in_button_target(ctx) -> tuple[int, int] | None:
    if pyautogui is None:
        raise RuntimeError("PyAutoGUI is required. Install dependencies with: pip install -r requirements.txt")

    ctx.check_stop()
    detection = ctx.config.raw.get("edge_sync_detection", {})
    region = tuple(detection.get("signin_region", EDGE_SIGN_IN_REGION))
    if len(region) != 4:
        region = EDGE_SIGN_IN_REGION
    region_x, region_y, _region_width, _region_height = (int(value) for value in region)

    screenshot = ctx.screenshot_region(region)
    ctx.check_stop()

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

    fallback = detection.get("signin_fallback_click", {})
    return (
        int(fallback.get("x", EDGE_SIGN_IN_FALLBACK_X)),
        int(fallback.get("y", EDGE_SIGN_IN_FALLBACK_Y)),
    )
