TEST_CASE = {
    "id": "TC_003_WEBVIEW_VERSION",
    "name": "Edge_WebView_Version_Evidence",
    "description": "Captures Microsoft Edge WebView registry version evidence from the Citrix desktop.",
    "evidence_name": "webview_evidence",
}


WEBVIEW_COMMAND = (
    "Get-itemproperty -path "
    "'HKLM:\\software\\Wow6432Node\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'"
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

    ctx.step("Start PowerShell inside Command Prompt so the registry cmdlet can run")
    ctx.type_text("powershell", interval=0.15)
    ctx.press("enter")
    ctx.wait(2.0)

    ctx.step(f"Step 6: Execute Edge WebView version command: {WEBVIEW_COMMAND}")
    ctx.type_text(WEBVIEW_COMMAND, interval=0.15)
    ctx.press("enter")
    ctx.wait(3.0)

    ctx.step("Step 7: Command render wait completed. Runner will capture and copy the final screenshot.")
