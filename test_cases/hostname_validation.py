TEST_CASE = {
    "id": "TC_001_HOSTNAME",
    "name": "Hostname_Validation",
    "description": "Runs hostname in Citrix Command Prompt and captures evidence only after output is visible.",
    "show_in_gui": False,
}

def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter the Citrix Desktop Name before starting the test.")

    ctx.step(f"Step 1: Detect Citrix session window: {desktop_name}")
    ctx.activate_window_by_title(
        desktop_name,
        exact=True,
        wait_after_sec=ctx.config.wait("citrix_activation_wait_sec", 4.0),
    )

    ctx.step("Step 2: Activate Citrix input focus with a center-screen click")
    ctx.click_screen_center(wait_after_sec=ctx.config.wait("citrix_focus_click_wait_sec", 1.0))

    ctx.step("Step 3: Open Run dialog using Windows + R")
    ctx.hotkey("winleft", "r")
    ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))

    ctx.step("Step 4: Launch Command Prompt")
    ctx.type_text("cmd")
    ctx.press("enter")
    ctx.wait(ctx.config.wait("cmd_launch_wait_sec", 3.0))

    ctx.step("Step 5: Execute hostname command")
    ctx.type_text("hostname")
    ctx.press("enter")
    ctx.wait(ctx.config.wait("after_hostname_enter_wait_sec", 1.0))

    ctx.step("Step 6: Verify hostname output is present before screenshot capture")
    console_text = ctx.copy_selected_text_from_active_window()
    _verify_hostname_output(console_text)

    ctx.step("Hostname output verified. Runner will now capture and copy the final pass screenshot.")


def _verify_hostname_output(console_text):
    lines = [line.strip() for line in console_text.splitlines() if line.strip()]
    lower_lines = [line.lower() for line in lines]

    hostname_command_index = -1
    for index, line in enumerate(lower_lines):
        if line.endswith(">hostname") or line == "hostname" or line.endswith(" hostname"):
            hostname_command_index = index

    if hostname_command_index == -1:
        raise RuntimeError("Command Prompt text did not include the executed hostname command.")

    output_lines = lines[hostname_command_index + 1 :]
    prompt_like_lines = [line for line in output_lines if ">" in line]
    hostname_lines = [
        line
        for line in output_lines
        if line not in prompt_like_lines and line.lower() != "hostname"
    ]

    if not hostname_lines:
        raise RuntimeError("No hostname output appeared after executing the hostname command.")
