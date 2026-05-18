TEST_CASE = {
    "id": "TC_012_SHAKEDOWN_WINDOWS_VERSION",
    "name": "Shakedown_Windows_Version_Evidence",
    "description": "Opens winver and captures Windows OS version evidence.",
    "capture_screenshot": False,
}


WINVER_COMMAND = "winver"


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    winver_opened = False
    try:
        ctx.step(f"Step 1: Activate Citrix desktop using user input: {desktop_name}")
        ctx.activate_window_by_title(
            desktop_name,
            exact=False,
            wait_after_sec=ctx.config.wait("citrix_activation_wait_sec", 4.0),
        )

        ctx.step("Step 2: Ensure Citrix input focus with a center-screen click")
        ctx.click_screen_center(wait_after_sec=ctx.config.wait("citrix_focus_click_wait_sec", 1.0))

        ctx.step(f"Step 3: Open Windows Version dialog using Run command: {WINVER_COMMAND}")
        ctx.hotkey("winleft", "r")
        ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))
        ctx.type_text(WINVER_COMMAND, interval=0.15)
        ctx.press("enter")
        ctx.wait(ctx.config.wait("winver_dialog_wait_sec", 3.0))
        winver_opened = True

        ctx.step("Step 4: Capture Windows version evidence screenshot and copy it to clipboard")
        evidence_path = ctx.capture_evidence("winver")
        ctx.step(f"Windows version evidence file generated: {evidence_path}")

    finally:
        if winver_opened:
            ctx.step("Step 5: Close winver dialog with Enter")
            ctx.press("enter")
            ctx.wait(ctx.config.wait("winver_close_wait_sec", 1.0))
