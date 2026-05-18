TEST_CASE = {
    "id": "TC_016_IAT_CORE_APPLICATIONS",
    "name": "IAT_Core_Application_Test_Evidence",
    "description": "Searches Programs and Features for core enterprise applications and captures evidence.",
    "capture_screenshot": False,
}


PROGRAMS_AND_FEATURES_COMMAND = "appwiz.cpl"

APPLICATION_SEARCHES = [
    ("7-Zip", "7-zip", "7-zip_evidence"),
    ("Adobe Acrobat Reader", "Adobe Acrobat Reader", "adobe_acrobat_evidence"),
    ("Microsoft Office Apps", "apps", "Microsoft_Office_evidence"),
    ("Microsoft Visio", "visio", "Microsoft_Visio_evidence"),
    ("Microsoft Project", "project", "Microsoft_Project_evidence"),
    ("Citrix Components", "citrix", "citrix_vda_evidence"),
    ("OpenJDK / JRE", "JRE", "OpenJDK_JRE_evidence"),
    ("FSLogix Applications", "fslogix", "fslogix_apps_evidence"),
]


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    programs_opened = False
    try:
        ctx.step(f"Step 1: Activate Citrix desktop using user input: {desktop_name}")
        ctx.activate_window_by_title(
            desktop_name,
            exact=False,
            wait_after_sec=ctx.config.wait("citrix_activation_wait_sec", 4.0),
        )

        ctx.step("Step 2: Ensure Citrix input focus with a center-screen click")
        ctx.click_screen_center(wait_after_sec=ctx.config.wait("citrix_focus_click_wait_sec", 1.0))

        ctx.step(f"Step 3: Open Programs and Features using Run command: {PROGRAMS_AND_FEATURES_COMMAND}")
        ctx.hotkey("winleft", "r")
        ctx.wait(ctx.config.wait("run_dialog_wait_sec", 1.5))
        ctx.type_text(PROGRAMS_AND_FEATURES_COMMAND, interval=0.15)
        ctx.press("enter")
        ctx.wait(ctx.config.wait("iat_programs_features_open_wait_sec", 10.0))
        programs_opened = True

        ctx.step("Step 4: Maximize Programs and Features window")
        ctx.maximize_active_window()

        for index, (label, search_text, evidence_name) in enumerate(APPLICATION_SEARCHES, start=5):
            _search_and_capture_application(ctx, index, label, search_text, evidence_name)

    finally:
        if programs_opened:
            ctx.step("Step 13: Close Programs and Features with Alt + F4")
            ctx.hotkey("alt", "f4")
            ctx.wait(ctx.config.wait("iat_programs_features_close_wait_sec", 2.0))


def _search_and_capture_application(ctx, step_number, label, search_text, evidence_name):
    ctx.step(f"Step {step_number}: Search and capture - {label}")
    ctx.hotkey("ctrl", "f")
    ctx.wait(ctx.config.wait("iat_find_focus_wait_sec", 1.0))
    ctx.type_text(search_text, interval=0.15)
    ctx.press("enter")
    ctx.wait(ctx.config.wait("iat_search_result_wait_sec", 5.0))
    evidence_path = ctx.capture_evidence(evidence_name)
    ctx.step(f"{label} evidence file generated: {evidence_path}")
