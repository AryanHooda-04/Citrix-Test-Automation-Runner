TEST_CASE = {
    "id": "TC_008_APPLIST_VALIDATION",
    "name": "Applist_Validation_Evidence",
    "description": "Opens Applist from C:\\Temp, searches for NOT OK, and captures evidence.",
    "evidence_name": "applist_evidence",
}


TEMP_FOLDER = r"C:\Temp"
FILE_SEARCH_TEXT = "Applist"
IN_FILE_SEARCH_TEXT = "NOT OK"
DEFAULT_SEARCH_RESULT_DOUBLE_CLICK = {
    "x": 535,
    "y": 232,
}


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    ctx.step(f"File path accessed: {TEMP_FOLDER}")
    ctx.step(f"Search term used: {IN_FILE_SEARCH_TEXT}")

    ctx.step(f"Step 1: Activate Citrix desktop using user input: {desktop_name}")
    ctx.activate_window_by_title(
        desktop_name,
        exact=False,
        wait_after_sec=ctx.config.wait("citrix_activation_wait_sec", 4.0),
    )

    ctx.step("Step 2: Ensure Citrix input focus with a center-screen click")
    ctx.click_screen_center(wait_after_sec=ctx.config.wait("citrix_focus_click_wait_sec", 1.0))

    ctx.step("Step 3: Open newest Applist file directly from C:\\Temp")
    ctx.hotkey("winleft", "r")
    ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))
    ctx.type_text(
        'powershell -NoProfile -Command "notepad ((Get-ChildItem '
        f"'{TEMP_FOLDER}\\Applist*' -File | Sort-Object LastWriteTime -Descending | "
        'Select-Object -First 1).FullName)"',
        interval=0.15,
    )
    ctx.press("enter")
    ctx.wait(ctx.config.wait("applist_open_wait_sec", 5.0))

    ctx.step("Maximize Applist text file window")
    ctx.maximize_active_window()
    ctx.wait(ctx.config.wait("applist_notepad_after_maximize_wait_sec", 2.0))

    ctx.step(f"Step 6: Search for {IN_FILE_SEARCH_TEXT} inside the Applist file")
    ctx.hotkey("ctrl", "f")
    ctx.wait(ctx.config.wait("applist_find_dialog_wait_sec", 1.0))
    ctx.type_text(IN_FILE_SEARCH_TEXT, interval=0.15)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("applist_find_result_wait_sec", 2.0))

    ctx.step("Step 7: Applist search completed. Runner will capture and copy the final screenshot.")
