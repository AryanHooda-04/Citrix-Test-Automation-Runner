TEST_CASE = {
    "id": "TC_021_SILO43_BAD_FOLDER",
    "name": "Silo43_BAD_Folder_Evidence",
    "description": "Validates the Silo 43 BAD folder evidence shows a 2026 modified date.",
    "evidence_name": "silo43_bad_folder_evidence",
    "capture_screenshot": False,
}

BAD_FOLDER_PATH = "C:\\BAD\\"


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    explorer_opened = False
    try:
        ctx.step(f"Step 1: Activate Citrix desktop using user input: {desktop_name}")
        ctx.activate_window_by_title(
            desktop_name,
            exact=False,
            wait_after_sec=ctx.config.wait("citrix_activation_wait_sec", 4.0),
        )

        ctx.step("Step 2: Ensure Citrix input focus with a center-screen click")
        ctx.click_screen_center(wait_after_sec=ctx.config.wait("citrix_focus_click_wait_sec", 1.0))

        ctx.step(f"Step 3: Open BAD folder via Run: {BAD_FOLDER_PATH}")
        ctx.hotkey("winleft", "r")
        ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))
        ctx.type_text(BAD_FOLDER_PATH, interval=0.05)
        ctx.press("enter")
        ctx.wait(ctx.config.wait("silo43_bad_folder_open_wait_sec", 5.0))
        explorer_opened = True

        ctx.step("Step 4: Maximize File Explorer before evidence capture")
        ctx.maximize_active_window()
        ctx.wait(ctx.config.wait("silo43_bad_folder_after_maximize_wait_sec", 2.0))

        ctx.step("Step 5: Capture BAD folder evidence screenshot and copy it to clipboard")
        evidence_path = ctx.capture_evidence("silo43_bad_folder_evidence")
        ctx.step(f"BAD folder evidence file generated: {evidence_path}")
    finally:
        if explorer_opened:
            ctx.step("Step 6: Close File Explorer with Alt + F4")
            ctx.hotkey("alt", "f4")
            ctx.wait(ctx.config.wait("silo43_bad_folder_close_wait_sec", 2.0))
