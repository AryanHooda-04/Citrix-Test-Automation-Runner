TEST_CASE = {
    "id": "TC_014_SHAKEDOWN_FSLOGIX_PROFILE_LOG",
    "name": "Shakedown_FSLogix_Profile_Log_Evidence",
    "description": "Opens the FSLogix Profile log and searches for copy failure entries.",
    "capture_screenshot": False,
}


FSLOGIX_PROFILE_LOG_PATH = r"C:\ProgramData\FSLogix\Logs\Profile"
SEARCH_TERM = "copy failure"


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    notepad_opened = False
    try:
        ctx.step(f"Step 1: Activate Citrix desktop using user input: {desktop_name}")
        ctx.activate_window_by_title(
            desktop_name,
            exact=False,
            wait_after_sec=ctx.config.wait("citrix_activation_wait_sec", 4.0),
        )

        ctx.step("Step 2: Ensure Citrix input focus with a center-screen click")
        ctx.click_screen_center(wait_after_sec=ctx.config.wait("citrix_focus_click_wait_sec", 1.0))

        ctx.step("Step 3: Open latest FSLogix Profile log directly in Notepad")
        ctx.hotkey("winleft", "r")
        ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))
        ctx.type_text(
            'powershell -NoProfile -Command "notepad ((Get-ChildItem '
            f"'{FSLOGIX_PROFILE_LOG_PATH}\\*.log' -File | Sort-Object LastWriteTime -Descending | "
            'Select-Object -First 1).FullName)"',
            interval=0.15,
        )
        ctx.press("enter")
        ctx.wait(ctx.config.wait("fslogix_notepad_open_wait_sec", 2.0))
        notepad_opened = True

        ctx.step("Step 4: Maximize Notepad window")
        ctx.maximize_active_window()
        ctx.wait(ctx.config.wait("fslogix_notepad_after_maximize_wait_sec", 1.0))

        ctx.step(f"Step 5: Search FSLogix Profile log for: {SEARCH_TERM}")
        ctx.hotkey("ctrl", "f")
        ctx.wait(ctx.config.wait("fslogix_find_dialog_wait_sec", 1.0))
        ctx.type_text(SEARCH_TERM, interval=0.15)
        ctx.press("enter")
        ctx.wait(ctx.config.wait("fslogix_find_result_wait_sec", 2.0))

        ctx.step("Step 6: Capture FSLogix Profile log evidence screenshot and copy it to clipboard")
        evidence_path = ctx.capture_evidence("fslogix_profile_log")
        ctx.step(f"FSLogix Profile log evidence file generated: {evidence_path}")

    finally:
        if notepad_opened:
            ctx.step("Step 7: Close Notepad with Alt + F4")
            ctx.hotkey("alt", "f4")
            ctx.wait(ctx.config.wait("fslogix_notepad_close_wait_sec", 1.0))
