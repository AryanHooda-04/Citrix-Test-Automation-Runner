TEST_CASE = {
    "id": "TC_003_WEBVIEW_VERSION",
    "name": "Edge_WebView_Version_Evidence",
    "description": "Captures Microsoft Edge WebView registry version evidence from the Citrix desktop.",
    "evidence_name": "webview_evidence",
    "capture_screenshot": False,
}


WEBVIEW_COMMAND = (
    "Get-itemproperty -path "
    "'HKLM:\\software\\Wow6432Node\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'"
)

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

    ctx.step("Start PowerShell inside Command Prompt so the registry cmdlet can run")
    ctx.type_text("powershell", interval=0.15)
    ctx.press("enter")
    ctx.wait(2.0)

    ctx.step(f"Step 6: Execute Edge WebView version command: {WEBVIEW_COMMAND}")
    ctx.type_text(WEBVIEW_COMMAND, interval=0.15)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("webview_command_output_wait_sec", 5.0))

    ctx.step("Step 7: Capture Edge WebView evidence")
    webview_path = ctx.capture_evidence("webview_evidence")
    ctx.step(f"Edge WebView evidence file generated: {webview_path}")

    if not ctx.metadata.get("combine_edge_registry_evidence"):
        return

    ctx.step("Combined Edge registry evidence mode enabled. Reusing the same PowerShell session for Edge browser.")
    ctx.step("Step 8: Clear PowerShell output before Edge browser registry command")
    ctx.type_text("cls", interval=0.15)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("edge_combined_clear_wait_sec", 1.0))

    ctx.step(f"Step 9: Execute Edge browser version command: {EDGE_BROWSER_COMMAND}")
    ctx.type_text(EDGE_BROWSER_COMMAND, interval=0.15)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("edge_command_output_wait_sec", 5.0))

    ctx.step("Step 10: Capture Edge browser evidence")
    edge_path = ctx.capture_evidence("edge_evidence")
    ctx.step(f"Edge browser evidence file generated: {edge_path}")
    ctx.metadata["combined_edge_registry_evidence"] = True
    ctx.metadata["combined_edge_browser_screenshot"] = str(edge_path)
