TEST_CASE = {
    "id": "TC_020_SILO43_PING_PROD_DVFS",
    "name": "Silo43_Ping_Prod_DVFS_Evidence",
    "description": "Validates Silo 43 can ping prod.dvfs.com successfully.",
    "evidence_name": "silo43_ping_prod_dvfs_evidence",
}

PING_TARGET = "prod.dvfs.com"


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

    ctx.step(f"Step 6: Execute ping command: ping {PING_TARGET}")
    ctx.type_text(f"ping {PING_TARGET}", interval=0.05)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("silo43_ping_output_wait_sec", 8.0))

    ctx.step("Step 7: Ping output wait completed. Runner will capture and validate the final screenshot.")
