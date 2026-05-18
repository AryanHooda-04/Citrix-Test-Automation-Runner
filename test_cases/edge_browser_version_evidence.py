TEST_CASE = {
    "id": "TC_004_EDGE_BROWSER_VERSION",
    "name": "Edge_Browser_Version_Evidence",
    "description": "Captures Microsoft Edge browser registry version evidence from the Citrix desktop.",
    "evidence_name": "edge_evidence",
}


EDGE_BROWSER_COMMAND = (
    "Get-itemproperty -path "
    "'HKLM:\\software\\Wow6432Node\\Microsoft\\EdgeUpdate\\Clients\\{56EB18F8-B008-4CBD-B6D2-8C97FE7E9062}'"
)


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

    ctx.step("Step 4: Launch Command Prompt")
    ctx.type_text("cmd", interval=0.15)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("cmd_launch_wait_sec", 3.0))

    ctx.step("Step 5: Maximize Command Prompt")
    ctx.hotkey("alt", "space")
    ctx.wait(0.5)
    ctx.press("x")
    ctx.wait(1.0)

    ctx.step("Step 6: Start PowerShell inside Command Prompt")
    ctx.type_text("powershell", interval=0.15)
    ctx.press("enter")
    ctx.wait(2.0)

    ctx.step(f"Step 7: Execute Edge browser version command: {EDGE_BROWSER_COMMAND}")
    ctx.type_text(EDGE_BROWSER_COMMAND, interval=0.15)
    ctx.press("enter")
    ctx.wait(3.0)

    ctx.step("Step 8: Command render wait completed. Runner will capture and copy the final screenshot.")
