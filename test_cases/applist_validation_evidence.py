TEST_CASE = {
    "id": "TC_008_APPLIST_VALIDATION",
    "name": "Applist_Validation_Evidence",
    "description": "Opens Applist from C:\\Temp, searches for NOT OK, and captures evidence.",
    "evidence_name": "applist_evidence",
}


TEMP_FOLDER = r"C:\Temp"
FILE_SEARCH_TEXT = "Applist"
IN_FILE_SEARCH_TEXT = "NOT OK"
DEFAULT_SEARCH_RESULT_CLICK = {
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

    ctx.step(f"Step 3: Open File Explorer via Run at {TEMP_FOLDER}")
    ctx.hotkey("winleft", "r")
    ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))
    ctx.type_text(TEMP_FOLDER, interval=0.15)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("explorer_open_wait_sec", 3.0))

    ctx.step("Maximize File Explorer")
    ctx.maximize_active_window()
    ctx.wait(ctx.config.wait("explorer_after_maximize_wait_sec", 2.0))

    ctx.step(f"Step 4: Search for Applist file using Explorer search: {FILE_SEARCH_TEXT}")
    ctx.hotkey("ctrl", "f")
    ctx.wait(0.5)
    ctx.type_text(FILE_SEARCH_TEXT, interval=0.15)
    ctx.wait(ctx.config.wait("applist_search_results_wait_sec", 3.0))

    click_config = ctx.config.raw.get("applist_evidence", {}).get(
        "search_result_click",
        DEFAULT_SEARCH_RESULT_CLICK,
    )
    result_x = int(click_config.get("x", DEFAULT_SEARCH_RESULT_CLICK["x"]))
    result_y = int(click_config.get("y", DEFAULT_SEARCH_RESULT_CLICK["y"]))

    ctx.step(f"Step 5: Click first Applist search result at ({result_x}, {result_y}), then press Enter")
    click_wait_sec = ctx.config.wait("applist_search_result_click_wait_sec", 0.5)
    ctx.click(
        result_x,
        result_y,
        wait_after_sec=click_wait_sec,
    )
    ctx.wait(click_wait_sec)
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
