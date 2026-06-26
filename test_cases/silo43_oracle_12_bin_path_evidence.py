TEST_CASE = {
    "id": "TC_017_SILO43_ORACLE_12_BIN_PATH",
    "name": "Silo43_Oracle_12_Bin_Path_Evidence",
    "description": "Validates the Silo 43 PATH starts with the Oracle 12 32-bit client bin path.",
    "evidence_name": "silo43_oracle_12_bin_path_evidence",
}


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

    ctx.step("Step 6: Execute PATH echo command")
    ctx.type_text("echo %path%", interval=0.05)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("silo43_oracle_path_output_wait_sec", 3.0))

    ctx.step("Step 7: PATH output wait completed. Runner will capture and validate the final screenshot.")
