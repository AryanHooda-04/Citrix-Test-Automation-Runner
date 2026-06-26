from datetime import datetime


TEST_CASE = {
    "id": "TC_013_SHAKEDOWN_LOCAL_NETWORK_DRIVES",
    "name": "Shakedown_Local_Network_Drives_Evidence",
    "description": "Opens OneDrive, creates/deletes a folder, and captures local/network drive evidence.",
    "capture_screenshot": False,
}


SEARCH_RESULT_CLICK_X = 535
SEARCH_RESULT_CLICK_Y = 232


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

        folder_name = f"RunnerEvidence_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        ctx.step("Step 3: Open OneDrive folder directly via Run")
        ctx.hotkey("winleft", "r")
        ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))
        ctx.type_text('explorer "%UserProfile%\\OneDrive - Allianz"', interval=0.15)
        ctx.press("enter")
        ctx.wait(ctx.config.wait("onedrive_explorer_open_wait_sec", 5.0))
        explorer_opened = True

        ctx.step(f"Step 4: Create unique evidence folder in OneDrive: {folder_name}")
        ctx.hotkey("ctrl", "shift", "n")
        ctx.wait(ctx.config.wait("onedrive_new_folder_name_wait_sec", 1.0))
        ctx.type_text(folder_name, interval=0.05)
        ctx.press("enter")
        ctx.wait(ctx.config.wait("onedrive_folder_create_wait_sec", 2.0))

        ctx.step("Step 5: Maximize File Explorer before evidence capture")
        ctx.maximize_active_window()
        ctx.wait(ctx.config.wait("explorer_after_maximize_wait_sec", 2.0))

        ctx.step("Step 6: Capture local/network drives evidence after folder creation and copy it to clipboard")
        evidence_path = ctx.capture_evidence("local_network_drives")
        ctx.step(f"Local/network drives creation evidence file generated: {evidence_path}")

        ctx.step(f"Step 7: Search for and delete the unique evidence folder: {folder_name}")
        ctx.hotkey("ctrl", "f")
        ctx.wait(ctx.config.wait("onedrive_find_dialog_wait_sec", 2.0))
        ctx.type_text(folder_name, interval=0.05)
        ctx.wait(ctx.config.wait("onedrive_find_results_wait_sec", 10.0))
        ctx.click(
            x=SEARCH_RESULT_CLICK_X,
            y=SEARCH_RESULT_CLICK_Y,
            wait_after_sec=ctx.config.wait("onedrive_search_result_click_wait_sec", 1.0),
        )
        ctx.press("delete")
        ctx.wait(ctx.config.wait("onedrive_delete_prompt_wait_sec", 2.0))
        ctx.press("enter")
        ctx.wait(ctx.config.wait("onedrive_folder_delete_wait_sec", 10.0))
        ctx.press("enter")
        ctx.wait(ctx.config.wait("onedrive_after_location_popup_wait_sec", 2.0))

        ctx.step("Step 8: Clear File Explorer search before deletion evidence capture")
        ctx.hotkey("ctrl", "f")
        ctx.wait(ctx.config.wait("onedrive_find_dialog_wait_sec", 2.0))
        ctx.hotkey("ctrl", "a")
        ctx.wait(ctx.config.wait("onedrive_find_clear_wait_sec", 1.0))
        ctx.press("backspace")
        ctx.wait(ctx.config.wait("onedrive_after_search_clear_wait_sec", 5.0))

        ctx.step("Step 9: Capture local/network drives evidence after folder deletion and copy it to clipboard")
        deletion_evidence_path = ctx.capture_evidence("local_network_drives_deleted")
        ctx.step(f"Local/network drives deletion evidence file generated: {deletion_evidence_path}")

    finally:
        if explorer_opened:
            ctx.step("Step 10: Close File Explorer with Alt + F4")
            ctx.hotkey("alt", "f4")
            ctx.wait(ctx.config.wait("explorer_close_wait_sec", 2.0))
