TEST_CASE = {
    "id": "TC_005_ZSCALER_SERVICES",
    "name": "Zscaler_Services_Evidence",
    "description": "Opens ZCCVDI in the Citrix desktop and captures Zscaler evidence.",
    "evidence_name": "zscaler_evidence",
}

from core.zscaler_recovery import (
    recover_zscaler_connection_if_needed,
    zscaler_healthy_state_visible,
    zscaler_problem_state_visible,
)


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

    _launch_zscaler(ctx)
    _recover_or_retry_zscaler(ctx)

    ctx.step("Step 6: Application launch wait completed. Runner will capture and copy the final screenshot.")


def _launch_zscaler(ctx):
    ctx.step("Step 3: Open Windows Search using Windows + S")
    ctx.hotkey("winleft", "s")
    ctx.wait(ctx.config.wait("windows_search_wait_sec", 2.0))

    ctx.step(f"Step 4: Search and launch application: {APPLICATION_SEARCH_TEXT}")
    ctx.type_text(APPLICATION_SEARCH_TEXT, interval=0.15)
    ctx.wait(ctx.config.wait("windows_search_results_wait_sec", 5.0))
    ctx.press("enter")

    ctx.step("Step 5: Wait for Zscaler Client Connector VDI application to open")
    ctx.wait(ctx.config.wait("zscaler_launch_wait_sec", 5.0))


def _recover_or_retry_zscaler(ctx):
    for attempt in range(2):
        state = _poll_zscaler_state(ctx)
        if state == "healthy":
            return
        if state == "unknown" and not zscaler_problem_state_visible(ctx):
            ctx.step(
                "Zscaler OFF / CONNECTION ERROR state not visible after polling. "
                "Proceeding to evidence capture and full screenshot validation."
            )
            return

        recover_zscaler_connection_if_needed(ctx)
        state = _poll_zscaler_state(ctx)
        if state == "healthy":
            return
        if state == "unknown" and not zscaler_problem_state_visible(ctx):
            ctx.step(
                "Zscaler OFF / CONNECTION ERROR state not visible after recovery polling. "
                "Proceeding to evidence capture and full screenshot validation."
            )
            return

        problem_visible = zscaler_problem_state_visible(ctx)
        if attempt == 0:
            reason = "still shows OFF / CONNECTION ERROR" if problem_visible else "did not reach Service Status ON"
            ctx.step(f"Zscaler {reason} after Turn ON. Retrying testcase once.")
            ctx.hotkey("alt", "f4")
            ctx.wait(2.0)
            _launch_zscaler(ctx)
            continue

        raise RuntimeError("Zscaler still shows OFF / CONNECTION ERROR after retry.")


def _poll_zscaler_state(ctx):
    timeout_sec = ctx.config.wait("zscaler_status_poll_timeout_sec", 18.0)
    interval_sec = ctx.config.wait("zscaler_status_poll_interval_sec", 1.0)
    attempts = max(1, int(timeout_sec / interval_sec))
    for attempt in range(attempts):
        ctx.step(f"Zscaler status poll {attempt + 1} of {attempts}")
        if zscaler_healthy_state_visible(ctx):
            return "healthy"
        if zscaler_problem_state_visible(ctx):
            return "problem"
        ctx.wait(interval_sec)
    return "unknown"
