TEST_CASE = {
    "id": "TC_008_SHAKEDOWN_DESKTOP_AVAILABILITY",
    "name": "Shakedown_Desktop_Availability_Evidence",
    "description": "Minimizes open windows and captures desktop availability evidence.",
    "evidence_name": "desktop_availability",
}


SHOW_DESKTOP_X = 1910
SHOW_DESKTOP_Y = 1040


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

    show_desktop_wait = ctx.config.wait("show_desktop_wait_sec", 5.0)
    ctx.step(
        "Step 3: Click Show Desktop at bottom-right taskbar "
        f"coordinates ({SHOW_DESKTOP_X}, {SHOW_DESKTOP_Y}) and wait {show_desktop_wait} seconds"
    )
    ctx.click(SHOW_DESKTOP_X, SHOW_DESKTOP_Y, wait_after_sec=show_desktop_wait)

    ctx.step("Step 4: Desktop visibility wait completed. Runner will capture and copy the final screenshot.")
