TEST_CASE = {
    "id": "TC_009_SHAKEDOWN_ONEDRIVE_SYNC",
    "name": "Shakedown_OneDrive_Sync_Evidence",
    "description": "Opens OneDrive from Windows Search and captures sync availability evidence.",
    "evidence_name": "onedrive_sync",
    "capture_screenshot": False,
}


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    ctx.step(f"Step 1: Activate Citrix desktop using user input: {desktop_name}")
    ctx.activate_window_by_title(
        desktop_name,
        exact=False,
        wait_after_sec=ctx.config.wait("citrix_activation_wait_sec", 4.0),
    )

    ctx.step("Step 2: Ensure Citrix input focus with a center-screen click")
    ctx.click_screen_center(wait_after_sec=ctx.config.wait("citrix_focus_click_wait_sec", 1.0))

    ctx.step("Step 3: Open Windows Search using Windows + S")
    ctx.hotkey("winleft", "s")
    ctx.wait(ctx.config.wait("windows_search_wait_sec", 2.0))

    ctx.step("Step 4: Launch OneDrive from Windows Search")
    ctx.type_text(
        "Apps: OneDrive",
        interval=ctx.config.wait("citrix_typing_interval_sec", 0.15),
    )
    ctx.wait(ctx.config.wait("onedrive_sync_search_results_wait_sec", 15.0))
    ctx.press("enter")
    ctx.wait(ctx.config.wait("onedrive_sync_explorer_open_wait_sec", 10.0))

    ctx.step("Step 5: Maximize OneDrive File Explorer window")
    ctx.maximize_active_window()
    ctx.wait(ctx.config.wait("onedrive_sync_after_maximize_wait_sec", 2.0))

    ctx.step("Step 6: Capture OneDrive sync evidence screenshot and copy it to clipboard")
    ctx.capture_evidence("onedrive_sync")

    ctx.step("Step 7: Close OneDrive File Explorer window")
    ctx.hotkey("alt", "f4")
    ctx.wait(ctx.config.wait("onedrive_sync_explorer_close_wait_sec", 2.0))
