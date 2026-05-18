TEST_CASE = {
    "id": "TC_005_ZSCALER_SERVICES",
    "name": "Zscaler_Services_Evidence",
    "description": "Opens ZCCVDI in the Citrix desktop and captures Zscaler evidence.",
    "evidence_name": "zscaler_evidence",
}


APPLICATION_SEARCH_TEXT = "Apps: ZCCVDI"


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

    ctx.step("Step 3: Open Windows Search using Windows + S")
    ctx.hotkey("winleft", "s")
    ctx.wait(ctx.config.wait("windows_search_wait_sec", 2.0))

    ctx.step(f"Step 4: Search and launch application: {APPLICATION_SEARCH_TEXT}")
    ctx.type_text(APPLICATION_SEARCH_TEXT, interval=0.15)
    ctx.wait(ctx.config.wait("windows_search_results_wait_sec", 5.0))
    ctx.press("enter")

    ctx.step("Step 5: Wait for Zscaler Client Connector VDI application to open")
    ctx.wait(ctx.config.wait("zscaler_launch_wait_sec", 5.0))

    ctx.step("Step 6: Application launch wait completed. Runner will capture and copy the final screenshot.")
