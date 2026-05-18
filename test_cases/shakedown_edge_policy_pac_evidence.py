TEST_CASE = {
    "id": "TC_011_SHAKEDOWN_EDGE_POLICY_PAC",
    "name": "Shakedown_Edge_Policy_PAC_Evidence",
    "description": "Opens edge://policy and captures policy evidence at two scroll positions.",
    "capture_screenshot": False,
}


EDGE_SEARCH_TEXT = "Apps: Microsoft Edge"
EDGE_POLICY_URL = "edge://policy"


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    edge_opened = False
    try:
        ctx.step(f"Step 1: Activate Citrix desktop using user input: {desktop_name}")
        ctx.activate_window_by_title(
            desktop_name,
            exact=False,
            wait_after_sec=ctx.config.wait("citrix_activation_wait_sec", 4.0),
        )

        ctx.step("Step 2: Ensure Citrix input focus with a center-screen click")
        ctx.click_screen_center(wait_after_sec=ctx.config.wait("citrix_focus_click_wait_sec", 1.0))

        ctx.step("Step 3: Open Microsoft Edge from Windows Search")
        ctx.hotkey("winleft", "s")
        ctx.wait(ctx.config.wait("windows_search_wait_sec", 2.0))
        ctx.type_text(EDGE_SEARCH_TEXT, interval=0.15)
        ctx.wait(ctx.config.wait("edge_search_results_wait_sec", 10.0))
        ctx.press("enter")
        ctx.wait(ctx.config.wait("edge_launch_wait_sec", 10.0))
        edge_opened = True

        ctx.step("Step 4: Maximize Microsoft Edge window")
        ctx.maximize_active_window()

        ctx.step(f"Step 5: Navigate to Edge policy page: {EDGE_POLICY_URL}")
        ctx.hotkey("alt", "d")
        ctx.wait(0.5)
        ctx.type_text(EDGE_POLICY_URL, interval=0.15)
        ctx.press("enter")
        ctx.wait(ctx.config.wait("edge_policy_page_wait_sec", 5.0))

        scroll_wait = ctx.config.wait("edge_policy_scroll_wait_sec", 2.0)

        ctx.step("Step 6: Press Page Down once and capture policy evidence part 1")
        ctx.press("pagedown", presses=1)
        ctx.wait(scroll_wait)
        policy_part_1 = ctx.capture_evidence("policy_pac_1")
        ctx.step(f"Policy evidence part 1 file generated: {policy_part_1}")

        ctx.step("Step 7: Press Page Down twice and capture policy evidence part 2")
        ctx.press("pagedown", presses=2)
        ctx.wait(scroll_wait)
        policy_part_2 = ctx.capture_evidence("policy_pac_2")
        ctx.step(f"Policy evidence part 2 file generated: {policy_part_2}")

    finally:
        if edge_opened:
            ctx.step("Step 8: Close Microsoft Edge with Alt + F4")
            ctx.hotkey("alt", "f4")
            ctx.wait(ctx.config.wait("edge_close_wait_sec", 2.0))
