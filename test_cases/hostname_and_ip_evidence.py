TEST_CASE = {
    "id": "TC_002_HOSTNAME_IP",
    "name": "Hostname_and_IP_Evidence",
    "description": "Runs hostname and ipconfig in Citrix Command Prompt, then captures IP evidence.",
}

def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter the Citrix Desktop Name before starting the test.")

    ctx.step(f"Step 1: Activate Citrix desktop window: {desktop_name}")
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

    ctx.step("Step 4: Launch Command Prompt")
    ctx.type_text("cmd")
    ctx.press("enter")
    ctx.wait(ctx.config.wait("cmd_launch_wait_sec", 3.0))

    ctx.step("Step 5: Maximize Command Prompt")
    ctx.hotkey("alt", "space")
    ctx.wait(0.5)
    ctx.press("x")
    ctx.wait(1.0)

    ctx.step("Step 6: Execute hostname command without validation")
    ctx.type_text("hostname")
    ctx.press("enter")
    ctx.wait(ctx.config.wait("after_hostname_command_wait_sec", 2.0))

    ctx.step("Step 7: Execute ipconfig command")
    ctx.type_text("ipconfig")
    ctx.press("enter")
    ctx.wait(ctx.config.wait("after_ipconfig_enter_wait_sec", 3.0))

    ctx.step("Step 8: ipconfig wait completed. Runner will now capture and copy the final pass screenshot.")
