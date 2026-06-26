from core.automation_context import evidence_category_path
from core.ocr_validation import validate_silo43_oracle_12_bin_path_with_windows_ocr
from core.test_categories import SILO43_EVIDENCE_FOLDER


TEST_CASE = {
    "id": "TC_018_SILO43_NICE_ENV_VARIABLES",
    "name": "Silo43_Nice_Env_Variables_Evidence",
    "description": "Validates the Silo 43 PATH has the NICE codec and release paths immediately after Oracle 12.",
    "capture_screenshot": False,
}

ORACLE_PATH_EVIDENCE_PREFIX = "silo43_oracle_12_bin_path_evidence"


def run(ctx):
    desktop_name = (ctx.citrix_desktop_name or "").strip()
    if not desktop_name:
        raise RuntimeError("Please enter Citrix Desktop Name.")

    ctx.step("Step 1: Reuse the Oracle 12 PATH screenshot captured in the previous Silo 43 testcase")
    screenshots_dir = evidence_category_path(
        ctx.config.path("screenshots_dir"),
        desktop_name,
        SILO43_EVIDENCE_FOLDER,
    )
    screenshot_path = _latest_oracle_path_screenshot(screenshots_dir, ctx.step)
    if screenshot_path is None:
        raise RuntimeError(
            "A valid Oracle 12 PATH evidence screenshot was not found. "
            "Run Silo43_Oracle_12_Bin_Path_Evidence before Nice Env Variables."
        )

    ctx.evidence_paths.append(screenshot_path)
    ctx.step(f"Reusing PATH evidence screenshot: {screenshot_path}")
    ctx.step("Step 2: Runner will validate NICE PATH entries from the reused screenshot.")


def _latest_oracle_path_screenshot(screenshots_dir, log_step):
    if not screenshots_dir.exists():
        return None
    screenshots = sorted(
        screenshots_dir.glob(f"{ORACLE_PATH_EVIDENCE_PREFIX}_*.png"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for screenshot in screenshots:
        if "_Pass_" not in screenshot.name:
            log_step(f"Skipping non-pass Oracle PATH screenshot for NICE reuse: {screenshot}")
            continue
        result = validate_silo43_oracle_12_bin_path_with_windows_ocr(screenshot)
        if result.valid:
            return screenshot
        log_step(
            "Skipping Oracle PATH screenshot for NICE reuse because Oracle validation failed: "
            f"{result.reason}"
        )
    return None
