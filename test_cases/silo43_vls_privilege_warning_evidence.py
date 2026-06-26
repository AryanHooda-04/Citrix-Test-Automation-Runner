TEST_CASE = {
    "id": "TC_019_SILO43_VLS_PRIVILEGE_WARNING",
    "name": "Silo43_VLS_Privilege_Warning_Evidence",
    "description": "Validates the Silo 43 VLS client privilege warning popup.",
    "evidence_name": "silo43_vls_privilege_warning_evidence",
    "capture_screenshot": False,
}

VLS_CLIENT_PATH = r"C:\apps\VLSClient\VLSMain.exe"


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

    ctx.step("Step 3: Open Run dialog using Windows + R")
    ctx.hotkey("winleft", "r")
    ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))

    ctx.step(f"Step 4: Launch VLS client: {VLS_CLIENT_PATH}")
    ctx.type_text(VLS_CLIENT_PATH, interval=0.02)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("silo43_vls_launch_wait_sec", 10.0))

    ctx.step("Step 5: Capture VLS privilege warning popup")
    vls_path = ctx.capture_evidence("silo43_vls_privilege_warning_evidence")
    ctx.step(f"VLS privilege warning evidence file generated: {vls_path}")
    ctx.wait(ctx.config.wait("silo43_vls_post_capture_wait_sec", 0.5))

    ctx.step("Step 6: Press Enter to dismiss the privilege warning")
    ctx.press("enter")
    ctx.step("Wait for the VLS follow-up popup to populate")
    ctx.wait(ctx.config.wait("silo43_vls_second_popup_wait_sec", 10.0))

    ctx.step("Step 7: Press Enter to handle the follow-up popup")
    ctx.press("enter")
    ctx.wait(ctx.config.wait("silo43_vls_after_close_wait_sec", 2.0))
