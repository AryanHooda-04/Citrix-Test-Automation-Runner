TEST_CASE = {
    "id": "TC_015_SHAKEDOWN_TEMP_FOLDER",
    "name": "Shakedown_Temp_Folder_Evidence",
    "description": "Opens C:\\Temp and captures temp folder hygiene evidence.",
    "capture_screenshot": False,
}


TEMP_FOLDER_PATH = r"C:\Temp"


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

        ctx.step(f"Step 3: Open TEMP folder via Run: {TEMP_FOLDER_PATH}")
        ctx.hotkey("winleft", "r")
        ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))
        ctx.type_text(TEMP_FOLDER_PATH, interval=0.15)
        ctx.press("enter")
        ctx.wait(ctx.config.wait("temp_folder_open_wait_sec", 10.0))
        explorer_opened = True

        ctx.step("Step 4: Maximize File Explorer before evidence capture")
        ctx.maximize_active_window()
        ctx.wait(ctx.config.wait("explorer_after_maximize_wait_sec", 2.0))

        ctx.step("Step 5: Capture TEMP folder evidence screenshot and copy it to clipboard")
        evidence_path = ctx.capture_evidence("temp_files")
        ctx.step(f"TEMP folder evidence file generated: {evidence_path}")

    finally:
        if explorer_opened:
            ctx.step("Step 6: Close File Explorer with Alt + F4")
            ctx.hotkey("alt", "f4")
            ctx.wait(ctx.config.wait("temp_folder_explorer_close_wait_sec", 2.0))
