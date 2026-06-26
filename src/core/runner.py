from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Callable

from PIL import Image

from core.automation_context import AutomationContext, evidence_category_path
from core.ai_validation import validate_hostname_ip_evidence, validate_screenshot_evidence
from core.config import AppConfig
from core.evidence_replacement import remove_existing_evidence_for_prefixes, remove_existing_evidence_for_test_case
from core.execution_log import ExecutionLog, desktop_scoped_path
from core.ocr_validation import (
    validate_7zip_programs_and_features_with_windows_ocr,
    validate_adobe_acrobat_programs_and_features_with_windows_ocr,
    validate_applist_evidence_with_windows_ocr,
    validate_desktop_availability_with_windows_ocr,
    validate_edge_browser_version_with_windows_ocr,
    validate_edge_settings_version_with_windows_ocr,
    validate_edge_sync_with_windows_ocr,
    validate_fslogix_apps_programs_and_features_with_windows_ocr,
    validate_fslogix_profile_log_with_windows_ocr,
    validate_google_access_with_windows_ocr,
    validate_hostname_ip_with_windows_ocr,
    validate_local_network_drives_created_with_windows_ocr,
    validate_local_network_drives_deleted_with_windows_ocr,
    validate_microsoft_office_programs_and_features_with_windows_ocr,
    validate_openjdk_jre_programs_and_features_with_windows_ocr,
    validate_microsoft_project_programs_and_features_with_windows_ocr,
    validate_microsoft_visio_programs_and_features_with_windows_ocr,
    validate_onedrive_sync_with_windows_ocr,
    validate_office_about_with_windows_ocr,
    validate_policy_pac_with_windows_ocr,
    validate_silo43_bad_folder_with_windows_ocr,
    validate_silo43_nice_env_variables_with_windows_ocr,
    validate_silo43_oracle_12_bin_path_with_windows_ocr,
    validate_silo43_ping_prod_dvfs_with_windows_ocr,
    validate_silo43_vls_privilege_warning_with_windows_ocr,
    validate_temp_folder_with_windows_ocr,
    validate_webview_version_with_windows_ocr,
    validate_windows_version_with_windows_ocr,
    validate_yahoo_access_with_windows_ocr,
    validate_zscaler_services_with_windows_ocr,
)
from core.screenshot import ScreenshotManager
from core.stop_control import StopRequested
from core.test_categories import evidence_category_for_test_name, should_skip_test_for_desktop, skip_reason_for_test_and_desktop
from core.test_loader import TestCase


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    test_case_name: str
    log_path: Path
    screenshot_path: Path | None
    evidence_paths: tuple[Path, ...] = ()
    error_message: str | None = None
    requires_manual_check: bool = False
    manual_check_message: str | None = None
    manual_confirmation_required: bool = False
    manual_confirmation_message: str | None = None
    manual_confirmation_screenshot: Path | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class HostnameIPValidationOutcome:
    manual_confirmation_required: bool = False
    manual_confirmation_message: str | None = None


EDGE_WEBVIEW_TEST_NAME = "Edge_WebView_Version_Evidence"
EDGE_BROWSER_TEST_NAME = "Edge_Browser_Version_Evidence"


def _timing_bucket(metadata: dict[str, object]) -> dict[str, object]:
    bucket = metadata.setdefault("timings_seconds", {})
    if not isinstance(bucket, dict):
        bucket = {}
        metadata["timings_seconds"] = bucket
    return bucket


def _record_timing(metadata: dict[str, object], key: str, started_at: float) -> float:
    elapsed = round(time.perf_counter() - started_at, 3)
    _timing_bucket(metadata)[key] = elapsed
    return elapsed


def _new_attempt_timing(metadata: dict[str, object], attempt_number: int) -> dict[str, object]:
    timings = _timing_bucket(metadata)
    attempts = timings.setdefault("attempts", [])
    if not isinstance(attempts, list):
        attempts = []
        timings["attempts"] = attempts
    attempt_timing: dict[str, object] = {"attempt": attempt_number}
    attempts.append(attempt_timing)
    return attempt_timing


def _record_attempt_timing(attempt_timing: dict[str, object], key: str, started_at: float) -> float:
    elapsed = round(time.perf_counter() - started_at, 3)
    attempt_timing[key] = elapsed
    return elapsed


class TestRunner:
    def __init__(
        self,
        config: AppConfig,
        citrix_desktop_name: str,
        status_callback: Callable[[str], None] | None = None,
        stop_event: Event | None = None,
        pause_event: Event | None = None,
        runtime_metadata: dict[str, object] | None = None,
    ) -> None:
        self.config = config
        self.citrix_desktop_name = citrix_desktop_name
        self.status_callback = status_callback or (lambda message: None)
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.runtime_metadata = dict(runtime_metadata or {})

    def run(self, test_case: TestCase) -> ExecutionResult:
        runner_started = time.perf_counter()
        run_metadata: dict[str, object] = dict(self.runtime_metadata)
        logs_dir = desktop_scoped_path(self.config.path("logs_dir"), self.citrix_desktop_name)
        evidence_category = evidence_category_for_test_name(test_case.name)
        screenshots_dir = evidence_category_path(
            self.config.path("screenshots_dir"),
            self.citrix_desktop_name,
            evidence_category,
        )
        execution_log = ExecutionLog(
            test_case_name=test_case.name,
            logs_dir=logs_dir,
            desktop_name=self.citrix_desktop_name,
        )

        if should_skip_test_for_desktop(test_case.name, self.citrix_desktop_name):
            skip_reason = skip_reason_for_test_and_desktop(test_case.name, self.citrix_desktop_name)
            execution_log.add_step(
                skip_reason,
                "INFO",
            )
            self.status_callback(skip_reason)
            _record_timing(run_metadata, "total_runner_seconds", runner_started)
            log_path = execution_log.finish("Skipped", None, [], run_metadata)
            return ExecutionResult("Skipped", test_case.name, log_path, None, (), metadata=run_metadata)

        def add_step(message: str, level: str = "INFO") -> None:
            execution_log.add_step(message, level)
            self.status_callback(message)

        replacement_started = time.perf_counter()
        deleted_count = remove_existing_evidence_for_test_case(
            screenshots_dir,
            test_case,
            lambda message: add_step(message, "INFO"),
        )
        _record_timing(run_metadata, "evidence_replacement_seconds", replacement_started)
        if deleted_count:
            add_step(f"Previous evidence replaced for this testcase: {deleted_count} screenshot(s) removed")

        if test_case.name == EDGE_WEBVIEW_TEST_NAME and run_metadata.get("combine_edge_registry_evidence"):
            extra_replacement_started = time.perf_counter()
            extra_deleted_count = remove_existing_evidence_for_prefixes(
                screenshots_dir,
                ("edge_evidence",),
                lambda message: add_step(message, "INFO"),
            )
            _record_timing(run_metadata, "combined_edge_evidence_replacement_seconds", extra_replacement_started)
            if extra_deleted_count:
                add_step(
                    "Previous Edge browser evidence replaced for combined Edge registry run: "
                    f"{extra_deleted_count} screenshot(s) removed"
                )

        context = AutomationContext(
            config=self.config,
            log_step=add_step,
            citrix_desktop_name=self.citrix_desktop_name,
            evidence_category=evidence_category,
            stop_event=self.stop_event,
            pause_event=self.pause_event,
        )
        context.metadata = run_metadata
        screenshots = ScreenshotManager(
            screenshots_dir=screenshots_dir,
            settle_seconds=self.config.wait("screenshot_settle_sec", 0.8),
            stop_event=self.stop_event,
            pause_event=self.pause_event,
            desktop_name=self.citrix_desktop_name,
            suppress_local_notifications=bool(
                self.config.screenshot_settings.get("suppress_local_notifications", True)
            ),
            notification_guard_wait_seconds=self.config.wait("notification_guard_wait_sec", 0.8),
        )
        screenshot_path: Path | None = None
        manual_confirmation_required = False
        manual_confirmation_message: str | None = None
        retained_validation_failure_paths: list[Path] = []

        try:
            attempt_limit = _validation_attempt_limit(test_case.name, self.config.raw)
            attempt_number = 1

            while True:
                attempt_started = time.perf_counter()
                attempt_timing = _new_attempt_timing(run_metadata, attempt_number)
                if attempt_number == 1:
                    add_step(f"Starting test case: {test_case.name}")
                else:
                    add_step(
                        "Re-running test case after validation failure "
                        f"(attempt {attempt_number} of {attempt_limit})"
                    )
                    context = AutomationContext(
                        config=self.config,
                        log_step=add_step,
                        citrix_desktop_name=self.citrix_desktop_name,
                        evidence_category=evidence_category,
                        stop_event=self.stop_event,
                        pause_event=self.pause_event,
                    )
                    context.metadata = run_metadata
                    screenshots.capture_region = None
                    screenshot_path = None

                add_step(f"Desktop name entered by user: {self.citrix_desktop_name}")

                try:
                    automation_started = time.perf_counter()
                    try:
                        test_case.run(context)
                    finally:
                        _record_attempt_timing(attempt_timing, "automation_seconds", automation_started)
                    screenshots.capture_region = context.capture_region()
                    add_step("Automation script completed successfully")

                    validation_started = time.perf_counter()
                    try:
                        if _should_validate_web_access_evidence(test_case.name):
                            _validate_web_access_evidence_screenshots(context.evidence_paths, add_step, self.config.raw)
                        elif _should_validate_office_evidence(test_case.name):
                            _validate_office_evidence_screenshots(context.evidence_paths, add_step, self.config.raw)
                        elif _should_validate_shakedown_edge_sync_evidence(test_case.name):
                            _validate_shakedown_edge_sync_evidence_screenshots(
                                context.evidence_paths,
                                add_step,
                                self.config.raw,
                            )
                        elif _should_validate_policy_pac_evidence(test_case.name):
                            _validate_policy_pac_evidence_screenshots(context, add_step, self.config.raw)
                        elif _should_validate_local_network_drives_evidence(test_case.name):
                            _validate_local_network_drives_evidence_screenshots(
                                context.evidence_paths,
                                add_step,
                                self.config.raw,
                            )
                        elif _should_validate_fslogix_profile_log_evidence(test_case.name):
                            _validate_fslogix_profile_log_evidence_screenshots(
                                context.evidence_paths,
                                add_step,
                                self.config.raw,
                            )
                        elif _should_validate_temp_folder_evidence(test_case.name):
                            _validate_temp_folder_evidence_screenshots(context.evidence_paths, add_step, self.config.raw)
                        elif _should_validate_iat_core_application_evidence(test_case.name):
                            _validate_iat_core_application_evidence_screenshots(context, add_step, self.config.raw)
                        elif _should_validate_webview_evidence(test_case.name) and context.evidence_paths:
                            _validate_webview_context_screenshots(context.evidence_paths, add_step, self.config.raw)
                        elif _should_validate_silo43_nice_env_variables_evidence(test_case.name):
                            _validate_silo43_nice_env_variables_screenshot(context.evidence_paths, add_step)
                        elif _should_validate_silo43_vls_privilege_warning_evidence(test_case.name):
                            _validate_silo43_vls_privilege_warning_screenshot(context.evidence_paths, add_step)
                        elif _should_validate_silo43_bad_folder_evidence(test_case.name):
                            _validate_silo43_bad_folder_screenshot(context.evidence_paths, add_step)
                    finally:
                        _record_attempt_timing(attempt_timing, "pre_screenshot_validation_seconds", validation_started)

                    if test_case.capture_screenshot and self.config.screenshot_settings.get("capture_on_pass", True):
                        screenshot_name = test_case.evidence_name or test_case.name
                        screenshot_started = time.perf_counter()
                        screenshot_path = screenshots.capture(screenshot_name, "Pass")
                        _record_attempt_timing(attempt_timing, "pass_screenshot_capture_seconds", screenshot_started)
                        context.evidence_paths.append(screenshot_path)
                        add_step(f"Pass screenshot saved: {screenshot_path}")

                        screenshot_validation_started = time.perf_counter()
                        try:
                            if _should_validate_hostname_ip_evidence(test_case.name, self.config.raw):
                                validation_outcome = _validate_hostname_ip_pass_screenshot(
                                    screenshot_path,
                                    self.config.raw,
                                    self.citrix_desktop_name,
                                    context.evidence_paths,
                                    add_step,
                                )
                                manual_confirmation_required = validation_outcome.manual_confirmation_required
                                manual_confirmation_message = validation_outcome.manual_confirmation_message
                            elif _should_validate_edge_browser_evidence(test_case.name):
                                _validate_edge_browser_pass_screenshot(
                                    screenshot_path,
                                    context.evidence_paths,
                                    add_step,
                                    self.config.raw,
                                )
                            elif _should_validate_webview_evidence(test_case.name):
                                _validate_webview_pass_screenshot(
                                    screenshot_path,
                                    context.evidence_paths,
                                    add_step,
                                    self.config.raw,
                                )
                            elif _should_validate_zscaler_evidence(test_case.name):
                                _validate_zscaler_pass_screenshot(
                                    screenshot_path,
                                    context.evidence_paths,
                                    add_step,
                                    self.config.raw,
                                )
                            elif _should_validate_desktop_availability_evidence(test_case.name):
                                _validate_desktop_availability_pass_screenshot(
                                    screenshot_path,
                                    context.evidence_paths,
                                    add_step,
                                    self.config.raw,
                                )
                            elif _should_validate_onedrive_sync_evidence(test_case.name):
                                _validate_onedrive_sync_pass_screenshot(
                                    screenshot_path,
                                    context.evidence_paths,
                                    add_step,
                                    self.config.raw,
                                )
                            elif _should_validate_windows_version_evidence(test_case.name):
                                _validate_windows_version_pass_screenshot(
                                    screenshot_path,
                                    context.evidence_paths,
                                    add_step,
                                    self.config.raw,
                                )
                            elif _should_record_applist_evidence_metadata(test_case.name):
                                _record_applist_evidence_metadata(screenshot_path, context, add_step, self.config.raw)
                            elif _should_validate_silo43_oracle_12_bin_path_evidence(test_case.name):
                                _validate_silo43_oracle_12_bin_path_pass_screenshot(
                                    screenshot_path,
                                    context.evidence_paths,
                                    add_step,
                                )
                            elif _should_validate_silo43_ping_prod_dvfs_evidence(test_case.name):
                                _validate_silo43_ping_prod_dvfs_pass_screenshot(
                                    screenshot_path,
                                    context.evidence_paths,
                                    add_step,
                                )
                        finally:
                            _record_attempt_timing(
                                attempt_timing,
                                "pass_screenshot_validation_seconds",
                                screenshot_validation_started,
                            )

                        if self.config.screenshot_settings.get("copy_pass_screenshot_to_clipboard", True):
                            clipboard_started = time.perf_counter()
                            screenshots.copy_to_clipboard(screenshot_path)
                            _record_attempt_timing(attempt_timing, "clipboard_copy_seconds", clipboard_started)
                            add_step("Pass screenshot copied to clipboard")
                    if retained_validation_failure_paths:
                        _delete_retained_failure_evidence_files(retained_validation_failure_paths, add_step)
                    _record_attempt_timing(attempt_timing, "attempt_total_seconds", attempt_started)
                    break
                except (AIValidationFailed, EvidenceValidationFailed) as exc:
                    _record_attempt_timing(attempt_timing, "attempt_total_seconds", attempt_started)
                    if _should_remove_validation_evidence_on_failure(test_case.name):
                        _delete_retained_failure_evidence_files(retained_validation_failure_paths, add_step)
                        retained_validation_failure_paths.extend(
                            _preserve_retry_evidence_files(context.evidence_paths, add_step)
                        )
                        if retained_validation_failure_paths:
                            screenshot_path = retained_validation_failure_paths[-1]
                    if attempt_number >= attempt_limit:
                        add_step("Max validation attempts reached -> Marking as FAIL", "ERROR")
                        raise
                    add_step(
                        "Validation Failed -> Retrying "
                        f"(Attempt {attempt_number + 1} of {attempt_limit}): {exc.reason}",
                        "WARNING",
                    )
                    _cleanup_before_validation_retry(test_case.name, context, self.config, add_step)
                    attempt_number += 1
                    continue

            _record_timing(run_metadata, "total_runner_seconds", runner_started)
            log_path = execution_log.finish("Pass", screenshot_path, context.evidence_paths, context.metadata)
            return ExecutionResult(
                "Pass",
                test_case.name,
                log_path,
                screenshot_path,
                tuple(context.evidence_paths),
                manual_confirmation_required=manual_confirmation_required,
                manual_confirmation_message=manual_confirmation_message,
                manual_confirmation_screenshot=screenshot_path if manual_confirmation_required else None,
                metadata=dict(context.metadata),
            )

        except StopRequested:
            add_step("Execution stopped by user", "ERROR")
            _record_timing(run_metadata, "total_runner_seconds", runner_started)
            log_path = execution_log.finish("Stopped", screenshot_path, context.evidence_paths, context.metadata)
            return ExecutionResult(
                "Stopped",
                test_case.name,
                log_path,
                screenshot_path,
                tuple(context.evidence_paths),
                metadata=dict(context.metadata),
            )

        except BaseException as exc:
            requires_manual_check = isinstance(exc, AIValidationFailed) and test_case.name == "Hostname_and_IP_Evidence"
            manual_check_message = None
            execution_log.set_error(exc)
            self.status_callback(f"Failure: {exc}")
            if requires_manual_check:
                manual_check_message = (
                    "Hostname_and_IP_Evidence failed AI validation after retry. "
                    "Please manually verify the hostname command output, ipconfig output, "
                    "and screenshot overlay before running the remaining testcases."
                )
                execution_log.add_step(manual_check_message, "ERROR")
            screenshots.capture_region = context.capture_region()
            preserved_failure_paths = [path for path in retained_validation_failure_paths if path.exists()]
            if preserved_failure_paths:
                for path in preserved_failure_paths:
                    if path not in context.evidence_paths:
                        context.evidence_paths.append(path)
                if screenshot_path is None or not screenshot_path.exists():
                    screenshot_path = preserved_failure_paths[-1]

            if (
                test_case.capture_screenshot
                and self.config.screenshot_settings.get("capture_on_fail", True)
                and not preserved_failure_paths
            ):
                try:
                    screenshot_name = test_case.evidence_name or test_case.name
                    failure_screenshot_started = time.perf_counter()
                    screenshot_path = screenshots.capture(screenshot_name, "Fail")
                    _record_timing(run_metadata, "failure_screenshot_capture_seconds", failure_screenshot_started)
                    context.evidence_paths.append(screenshot_path)
                    execution_log.add_step(f"Failure screenshot saved: {screenshot_path}", "ERROR")
                    if self.config.screenshot_settings.get("copy_fail_screenshot_to_clipboard", False):
                        failure_clipboard_started = time.perf_counter()
                        screenshots.copy_to_clipboard(screenshot_path)
                        _record_timing(run_metadata, "failure_clipboard_copy_seconds", failure_clipboard_started)
                        execution_log.add_step("Failure screenshot copied to clipboard", "ERROR")
                except BaseException as screenshot_error:
                    execution_log.add_step(
                        f"Unable to capture failure screenshot: {screenshot_error}",
                        "ERROR",
                    )

            _record_timing(run_metadata, "total_runner_seconds", runner_started)
            log_path = execution_log.finish("Fail", screenshot_path, context.evidence_paths, context.metadata)
            return ExecutionResult(
                "Fail",
                test_case.name,
                log_path,
                screenshot_path,
                tuple(context.evidence_paths),
                str(exc),
                requires_manual_check,
                manual_check_message,
                metadata=dict(context.metadata),
            )


def _should_validate_hostname_ip_evidence(test_case_name: str, raw_config: dict) -> bool:
    ai_settings = raw_config.get("ai_validation", {})
    return (
        test_case_name == "Hostname_and_IP_Evidence"
        and bool(ai_settings.get("enabled", False))
        and bool(ai_settings.get("hostname_ip_enabled", True))
    )


def _hostname_ip_validation_attempt_limit(test_case_name: str, raw_config: dict) -> int:
    if not _should_validate_hostname_ip_evidence(test_case_name, raw_config):
        return 1
    ai_settings = raw_config.get("ai_validation", {})
    try:
        return max(1, int(ai_settings.get("hostname_ip_max_attempts", 1)))
    except (TypeError, ValueError):
        return 1


def _validation_attempt_limit(test_case_name: str, raw_config: dict) -> int:
    if (
        _should_validate_edge_browser_evidence(test_case_name)
        or _should_validate_webview_evidence(test_case_name)
        or _should_validate_desktop_availability_evidence(test_case_name)
        or _should_validate_onedrive_sync_evidence(test_case_name)
        or _should_validate_windows_version_evidence(test_case_name)
        or _should_validate_shakedown_edge_sync_evidence(test_case_name)
        or _should_validate_policy_pac_evidence(test_case_name)
        or _should_validate_local_network_drives_evidence(test_case_name)
        or _should_validate_fslogix_profile_log_evidence(test_case_name)
        or _should_validate_temp_folder_evidence(test_case_name)
        or _should_validate_web_access_evidence(test_case_name)
        or _should_validate_office_evidence(test_case_name)
        or _should_record_applist_evidence_metadata(test_case_name)
        or _should_validate_iat_core_application_evidence(test_case_name)
        or _should_validate_silo43_oracle_12_bin_path_evidence(test_case_name)
        or _should_validate_silo43_vls_privilege_warning_evidence(test_case_name)
        or _should_validate_silo43_ping_prod_dvfs_evidence(test_case_name)
        or _should_validate_silo43_bad_folder_evidence(test_case_name)
    ):
        return 2
    return _hostname_ip_validation_attempt_limit(test_case_name, raw_config)


class AIValidationFailed(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"AI validation failed: {reason}")


class EvidenceValidationFailed(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _short_validation_text(raw_text: str, limit: int = 300) -> str:
    text = " ".join((raw_text or "").split())
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _log_ocr_failure_details(
    result,
    add_step: Callable[..., None],
    *,
    label: str = "OCR raw text",
) -> None:
    snippet = _short_validation_text(getattr(result, "raw_text", ""))
    if snippet:
        add_step(f"{label} snippet: {snippet}", "WARNING")


def _log_ai_failure_details(result, add_step: Callable[..., None]) -> None:
    cmd_hostname = (getattr(result, "cmd_hostname", "") or "").strip()
    overlay_hostname = (getattr(result, "overlay_hostname", "") or "").strip()
    ipv4_addresses = tuple(getattr(result, "ipv4_addresses", ()) or ())
    version = (getattr(result, "version", "") or "").strip()
    available = getattr(result, "available", None)
    fields = getattr(result, "fields", {}) or {}
    details: list[str] = []
    if cmd_hostname:
        details.append(f"cmd_hostname={cmd_hostname}")
    if overlay_hostname:
        details.append(f"overlay_hostname={overlay_hostname}")
    if ipv4_addresses:
        details.append(f"ipv4={', '.join(ipv4_addresses)}")
    if version:
        details.append(f"version={version}")
    if available is not None:
        details.append(f"available={available}")
    if isinstance(fields, dict):
        for key, value in fields.items():
            if value:
                details.append(f"{key}={value}")
    if details:
        add_step(f"AI parsed evidence fields: {'; '.join(details)}", "ERROR")


_AI_FALLBACK_DESCRIPTIONS: dict[str, str] = {
    "google_access": "The browser shows Google/Google Search or google.com successfully loaded. Reject browser error pages, blank pages, or unrelated websites.",
    "yahoo_access": "The browser shows Yahoo/yahoo.com successfully loaded. Reject browser error pages, blank pages, or unrelated websites.",
    "office_about_powerpoint": (
        "The screenshot must show the actual 'About Microsoft PowerPoint for Microsoft 365' modal/dialog, "
        "not just the PowerPoint Account page or About PowerPoint tile. The dialog must visibly contain a "
        "Session ID. License ID is optional when available. Copy the visible Session ID into fields.session_id "
        "and License ID into fields.license_id if present. Reject screenshots that only show version/channel "
        "details without Session ID."
    ),
    "edge_sync": "Microsoft Edge Settings is open on the Profiles page and the profile status shows 'Sync is on'. Reject if it only shows a Sign in button or sync is not enabled.",
    "edge_settings_version": "Microsoft Edge Settings About page is visible and shows a Microsoft Edge version number. Copy the visible version into version.",
    "policy_pac": "Microsoft Edge edge://policy page is visible. Validate the status/result column for the visible policies. Pass only when statuses are OK; fail if Error or NOT OK appears.",
    "local_network_created": "File Explorer is focused on a OneDrive folder and a newly created folder named RunnerEvidence_<timestamp> is visible before deletion.",
    "local_network_deleted": "File Explorer is focused on a OneDrive folder after deletion and no folder named RunnerEvidence_<timestamp> is visible in the file list.",
    "fslogix_profile_log": "A text editor or Notepad window is open with an FSLogix Profile log and the search term copy failure visible.",
    "temp_folder": "File Explorer is focused on the C:\\Temp folder or a Temp folder under This PC. Reject unrelated folders or terminal windows.",
    "applist": "An Applist text file is open in Notepad or a text editor and the in-document search/find box contains NOT OK.",
    "iat_7zip": "Programs and Features is open and the search box contains 7-zip. If 7-Zip is listed, copy its visible version into version and set available true; if no result is listed, set available false but keep valid true.",
    "iat_adobe": "Programs and Features is open and the search box contains Adobe Acrobat Reader. If Adobe Acrobat Reader is listed, copy its visible version into version and set available true; if no result is listed, set available false but keep valid true.",
    "iat_office": "Programs and Features is open and the search box contains apps. If Microsoft 365 Apps is listed, copy its visible version into version and set available true; if not listed, set available false but keep valid true.",
    "iat_visio": "Programs and Features is open and the search box contains visio. If Microsoft Visio is listed, copy its visible version into version and set available true; if not listed, set available false but keep valid true.",
    "iat_project": "Programs and Features is open and the search box contains project. If Microsoft Project is listed, copy its visible version into version and set available true; if not listed, set available false but keep valid true.",
    "iat_openjdk": "Programs and Features is open and the search box contains JRE. If Eclipse Temurin JRE is listed, copy its visible version into version and set available true; if not listed, set available false but keep valid true.",
    "iat_fslogix": "Programs and Features is open and the search box contains fslogix. If Microsoft FSLogix Apps is listed, copy its visible version into version and set available true; if not listed, set available false but keep valid true.",
    "edge_browser_registry": "A Command Prompt or PowerShell window shows a registry query result for Microsoft Edge and a pv/version value is visible. Copy the version into version.",
    "webview_registry": "A Command Prompt or PowerShell window shows a registry query result for Microsoft Edge WebView2 Runtime and a pv/version value is visible. Copy the version into version.",
    "zscaler": "The Zscaler Client Connector for VDI window is visible. Pass only if Service Status is ON and Authentication Status is Authenticated. Fail if Service Status is OFF or CONNECTION ERROR.",
    "desktop_availability": "The Citrix Windows desktop is visible and no foreground application window is open, aside from normal desktop wallpaper, icons, taskbar, or Citrix toolbar.",
    "onedrive_sync": "File Explorer is the focused window and it is open to a OneDrive folder. A OneDrive breadcrumb, OneDrive folder name, or OneDrive left navigation item should be visible.",
    "windows_version": "The About Windows dialog is open and a Windows release version such as 22H2, 23H2, or 24H2 is visible. Copy the release version into version.",
}


def _ai_fallback_enabled(raw_config: dict, test_case_name: str) -> bool:
    settings = raw_config.get("ai_validation", {})
    if not bool(settings.get("enabled", False)) or not bool(settings.get("fallback_enabled", False)):
        return False
    fallback_testcases = settings.get("fallback_testcases", {})
    if isinstance(fallback_testcases, dict):
        return bool(fallback_testcases.get(test_case_name, False))
    return bool(fallback_testcases)


def _try_ai_fallback(
    screenshot_path: Path,
    raw_config: dict,
    test_case_name: str,
    evidence_label: str,
    description_key: str,
    add_step: Callable[..., None],
    ocr_result=None,
) -> object | None:
    if not _ai_fallback_enabled(raw_config, test_case_name):
        return None

    if ocr_result is not None:
        reason = getattr(ocr_result, "reason", "") or "OCR validation did not pass."
        add_step(f"OCR Failed -> Switching to AI fallback for {evidence_label}: {reason}", "WARNING")
        _log_ocr_failure_details(ocr_result, add_step, label=f"{evidence_label} OCR raw text")

    description = _AI_FALLBACK_DESCRIPTIONS.get(description_key, description_key)
    add_step(f"AI validation started for {evidence_label}")
    result = validate_screenshot_evidence(
        screenshot_path,
        raw_config.get("ai_validation", {}),
        description=description,
        evidence_label=evidence_label,
    )
    if result.valid:
        _log_ai_generic_validation_pass(result, add_step, evidence_label)
        return result

    reason = result.reason or "OpenAI did not confirm valid evidence."
    add_step(f"AI validation failed for {evidence_label}: {reason}", "ERROR")
    _log_ai_failure_details(result, add_step)
    return None


def _log_ai_generic_validation_pass(result, add_step: Callable[..., None], evidence_label: str) -> None:
    details: list[str] = []
    version = (getattr(result, "version", "") or "").strip()
    available = getattr(result, "available", None)
    fields = getattr(result, "fields", {}) or {}
    if version:
        details.append(f"version={version}")
    if available is not None:
        details.append(f"available={available}")
    if isinstance(fields, dict):
        for key, value in fields.items():
            if value:
                details.append(f"{key}={value}")
    if not details and getattr(result, "reason", ""):
        details.append(str(result.reason))
    suffix = f": {'; '.join(details)}" if details else ""
    add_step(f"AI Validation Passed for {evidence_label}{suffix}")


def _validate_hostname_ip_pass_screenshot(
    screenshot_path: Path,
    raw_config: dict,
    desktop_name: str,
    evidence_paths: list[Path],
    add_step: Callable[..., None],
) -> HostnameIPValidationOutcome:
    add_step("OCR validation started for Hostname/IP evidence screenshot")
    ocr_result = validate_hostname_ip_with_windows_ocr(screenshot_path)
    if ocr_result.valid:
        ipv4_summary = ", ".join(ocr_result.ipv4_addresses) if ocr_result.ipv4_addresses else "detected"
        add_step(
            "OCR Validation Passed: "
            f"hostname={ocr_result.cmd_hostname}, IPv4={ipv4_summary}"
        )
        return HostnameIPValidationOutcome()

    add_step(f"OCR Failed -> Switching to AI: {ocr_result.reason}", "WARNING")
    _log_ocr_failure_details(ocr_result, add_step, label="Hostname/IP OCR raw text")
    add_step("AI validation started for Hostname/IP evidence screenshot")
    result = validate_hostname_ip_evidence(
        screenshot_path,
        raw_config.get("ai_validation", {}),
    )
    if result.valid:
        _log_ai_validation_pass(result, add_step)
        return HostnameIPValidationOutcome()

    reason = result.reason or "OpenAI did not confirm valid hostname/ipconfig evidence."
    add_step(f"AI validation failed: {reason}", "ERROR")
    _log_ai_failure_details(result, add_step)

    try:
        screenshot_path.unlink(missing_ok=True)
    except OSError as exc:
        add_step(f"Unable to remove invalid pass screenshot: {exc}", "ERROR")
    try:
        evidence_paths.remove(screenshot_path)
    except ValueError:
        pass
    raise AIValidationFailed(reason)


def _should_validate_webview_evidence(test_case_name: str) -> bool:
    return test_case_name == EDGE_WEBVIEW_TEST_NAME


def _should_validate_edge_browser_evidence(test_case_name: str) -> bool:
    return test_case_name == EDGE_BROWSER_TEST_NAME


def _should_validate_zscaler_evidence(test_case_name: str) -> bool:
    return test_case_name == "Zscaler_Services_Evidence"


def _should_validate_desktop_availability_evidence(test_case_name: str) -> bool:
    return test_case_name == "Shakedown_Desktop_Availability_Evidence"


def _should_validate_onedrive_sync_evidence(test_case_name: str) -> bool:
    return test_case_name == "Shakedown_OneDrive_Sync_Evidence"


def _should_validate_windows_version_evidence(test_case_name: str) -> bool:
    return test_case_name == "Shakedown_Windows_Version_Evidence"


def _should_validate_shakedown_edge_sync_evidence(test_case_name: str) -> bool:
    return test_case_name == "Shakedown_Edge_Sync_Evidence"


def _should_validate_policy_pac_evidence(test_case_name: str) -> bool:
    return test_case_name == "Shakedown_Edge_Policy_PAC_Evidence"


def _should_validate_local_network_drives_evidence(test_case_name: str) -> bool:
    return test_case_name == "Shakedown_Local_Network_Drives_Evidence"


def _should_validate_fslogix_profile_log_evidence(test_case_name: str) -> bool:
    return test_case_name == "Shakedown_FSLogix_Profile_Log_Evidence"


def _should_validate_temp_folder_evidence(test_case_name: str) -> bool:
    return test_case_name == "Shakedown_Temp_Folder_Evidence"


def _should_record_applist_evidence_metadata(test_case_name: str) -> bool:
    return test_case_name == "Applist_Validation_Evidence"


def _should_validate_iat_core_application_evidence(test_case_name: str) -> bool:
    return test_case_name == "IAT_Core_Application_Test_Evidence"


def _should_validate_web_access_evidence(test_case_name: str) -> bool:
    return test_case_name == "Google_and_Yahoo_Web_Access_Evidence"


def _should_validate_office_evidence(test_case_name: str) -> bool:
    return test_case_name == "Office_Applications_Launch"


def _should_validate_silo43_oracle_12_bin_path_evidence(test_case_name: str) -> bool:
    return test_case_name == "Silo43_Oracle_12_Bin_Path_Evidence"


def _should_validate_silo43_nice_env_variables_evidence(test_case_name: str) -> bool:
    return test_case_name == "Silo43_Nice_Env_Variables_Evidence"


def _should_validate_silo43_vls_privilege_warning_evidence(test_case_name: str) -> bool:
    return test_case_name == "Silo43_VLS_Privilege_Warning_Evidence"


def _should_validate_silo43_ping_prod_dvfs_evidence(test_case_name: str) -> bool:
    return test_case_name == "Silo43_Ping_Prod_DVFS_Evidence"


def _should_validate_silo43_bad_folder_evidence(test_case_name: str) -> bool:
    return test_case_name == "Silo43_BAD_Folder_Evidence"


def _should_remove_validation_evidence_on_failure(test_case_name: str) -> bool:
    return (
        _should_validate_office_evidence(test_case_name)
        or _should_validate_web_access_evidence(test_case_name)
        or _should_validate_desktop_availability_evidence(test_case_name)
        or _should_validate_onedrive_sync_evidence(test_case_name)
        or _should_validate_windows_version_evidence(test_case_name)
        or _should_validate_shakedown_edge_sync_evidence(test_case_name)
        or _should_validate_policy_pac_evidence(test_case_name)
        or _should_validate_local_network_drives_evidence(test_case_name)
        or _should_validate_fslogix_profile_log_evidence(test_case_name)
        or _should_validate_temp_folder_evidence(test_case_name)
        or _should_record_applist_evidence_metadata(test_case_name)
        or _should_validate_silo43_oracle_12_bin_path_evidence(test_case_name)
        or _should_validate_silo43_vls_privilege_warning_evidence(test_case_name)
        or _should_validate_silo43_ping_prod_dvfs_evidence(test_case_name)
        or _should_validate_silo43_bad_folder_evidence(test_case_name)
    )


def _validate_web_access_evidence_screenshots(
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    google_path = _latest_evidence_path(evidence_paths, "google_evidence")
    yahoo_path = _latest_evidence_path(evidence_paths, "yahoo_evidence")
    if google_path is None:
        raise EvidenceValidationFailed("Google evidence screenshot was not captured.")
    if yahoo_path is None:
        raise EvidenceValidationFailed("Yahoo evidence screenshot was not captured.")

    add_step("OCR validation started for Google web access evidence")
    google_result = validate_google_access_with_windows_ocr(google_path)
    if not google_result.valid:
        ai_result = _try_ai_fallback(
            google_path,
            raw_config,
            "Google_and_Yahoo_Web_Access_Evidence",
            "Google web access evidence",
            "google_access",
            add_step,
            google_result,
        )
        if ai_result is not None:
            add_step("Google web access validation recovered through AI fallback")
        else:
            add_step(f"Google web access validation failed: {google_result.reason}", "ERROR")
            raise EvidenceValidationFailed(google_result.reason)
    else:
        add_step("OCR Validation Passed: google.com is accessible")

    add_step("OCR validation started for Yahoo web access evidence")
    yahoo_result = validate_yahoo_access_with_windows_ocr(yahoo_path)
    if not yahoo_result.valid:
        ai_result = _try_ai_fallback(
            yahoo_path,
            raw_config,
            "Google_and_Yahoo_Web_Access_Evidence",
            "Yahoo web access evidence",
            "yahoo_access",
            add_step,
            yahoo_result,
        )
        if ai_result is not None:
            add_step("Yahoo web access validation recovered through AI fallback")
        else:
            add_step(f"Yahoo web access validation failed: {yahoo_result.reason}", "ERROR")
            raise EvidenceValidationFailed(yahoo_result.reason)
    else:
        add_step("OCR Validation Passed: yahoo.com is accessible")


def _validate_shakedown_edge_sync_evidence_screenshots(
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    sync_path = _latest_evidence_path(evidence_paths, "edge_sync")
    version_path = _latest_evidence_path(evidence_paths, "edge_browser_version")
    if sync_path is None:
        raise EvidenceValidationFailed("Edge sync evidence screenshot was not captured.")
    if version_path is None:
        raise EvidenceValidationFailed("Edge browser version evidence screenshot was not captured.")

    add_step("OCR validation started for Edge sync evidence screenshot")
    sync_result = validate_edge_sync_with_windows_ocr(sync_path)
    if not sync_result.valid:
        ai_result = _try_ai_fallback(
            sync_path,
            raw_config,
            "Shakedown_Edge_Sync_Evidence",
            "Edge sync evidence screenshot",
            "edge_sync",
            add_step,
            sync_result,
        )
        if ai_result is not None:
            add_step("Edge sync validation recovered through AI fallback")
        else:
            add_step(f"Edge sync validation failed: {sync_result.reason}", "ERROR")
            raise EvidenceValidationFailed(sync_result.reason)
    else:
        add_step('OCR Validation Passed: Edge sync shows "Sync is on"')

    add_step("OCR validation started for Edge browser version evidence screenshot")
    version_result = validate_edge_settings_version_with_windows_ocr(version_path)
    if not version_result.valid:
        ai_result = _try_ai_fallback(
            version_path,
            raw_config,
            "Shakedown_Edge_Sync_Evidence",
            "Edge browser version evidence screenshot",
            "edge_settings_version",
            add_step,
            version_result,
        )
        if ai_result is not None:
            add_step("Edge browser version validation recovered through AI fallback")
        else:
            add_step(f"Edge browser version validation failed: {version_result.reason}", "ERROR")
            raise EvidenceValidationFailed(version_result.reason)
    else:
        add_step(f"OCR Validation Passed: Edge browser version {version_result.version} is visible")


def _validate_policy_pac_evidence_screenshots(
    context: AutomationContext,
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    evidence_paths = context.evidence_paths
    policy_part_1 = _latest_evidence_path(evidence_paths, "policy_pac_1")
    policy_part_2 = _latest_evidence_path(evidence_paths, "policy_pac_2")
    if policy_part_1 is None:
        raise EvidenceValidationFailed("Policy PAC evidence part 1 screenshot was not captured.")
    if policy_part_2 is None:
        raise EvidenceValidationFailed("Policy PAC evidence part 2 screenshot was not captured.")

    context.metadata["policy_pac_not_ok_found"] = False
    context.metadata["policy_pac_error_found"] = False

    for label, path in (("part 1", policy_part_1), ("part 2", policy_part_2)):
        add_step(f"OCR validation started for Policy PAC evidence {label}")
        result = validate_policy_pac_with_windows_ocr(path)
        raw_lower = (result.raw_text or "").casefold()
        compact = "".join(raw_lower.split())
        if "not ok" in raw_lower or "notok" in compact:
            context.metadata["policy_pac_not_ok_found"] = True
        if "error" in raw_lower:
            context.metadata["policy_pac_error_found"] = True

        if not result.valid:
            ai_result = _try_ai_fallback(
                path,
                raw_config,
                "Shakedown_Edge_Policy_PAC_Evidence",
                f"Policy PAC evidence {label}",
                "policy_pac",
                add_step,
                result,
            )
            if ai_result is None:
                add_step(f"Policy PAC validation failed on {label}: {result.reason}", "ERROR")
                raise EvidenceValidationFailed(result.reason)

    add_step('Policy PAC validation passed: edge://policy is visible and the status column shows only "OK"')


def _validate_local_network_drives_evidence_screenshots(
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    created_path = _latest_evidence_path(evidence_paths, "local_network_drives")
    deleted_path = _latest_evidence_path(evidence_paths, "local_network_drives_deleted")
    if created_path is None:
        raise EvidenceValidationFailed("Local/network drives creation evidence screenshot was not captured.")
    if deleted_path is None:
        raise EvidenceValidationFailed("Local/network drives deletion evidence screenshot was not captured.")

    add_step("OCR validation started for local/network drives creation evidence screenshot")
    created_result = validate_local_network_drives_created_with_windows_ocr(created_path)
    if not created_result.valid:
        ai_result = _try_ai_fallback(
            created_path,
            raw_config,
            "Shakedown_Local_Network_Drives_Evidence",
            "local/network drives creation evidence screenshot",
            "local_network_created",
            add_step,
            created_result,
        )
        if ai_result is None:
            add_step(f"Local/network drives creation validation failed: {created_result.reason}", "ERROR")
            raise EvidenceValidationFailed(created_result.reason)
    else:
        add_step("OCR Validation Passed: OneDrive File Explorer is open and the RunnerEvidence folder is visible before deletion")

    add_step("OCR validation started for local/network drives deletion evidence screenshot")
    deleted_result = validate_local_network_drives_deleted_with_windows_ocr(deleted_path)
    if not deleted_result.valid:
        ai_result = _try_ai_fallback(
            deleted_path,
            raw_config,
            "Shakedown_Local_Network_Drives_Evidence",
            "local/network drives deletion evidence screenshot",
            "local_network_deleted",
            add_step,
            deleted_result,
        )
        if ai_result is None:
            add_step(f"Local/network drives deletion validation failed: {deleted_result.reason}", "ERROR")
            raise EvidenceValidationFailed(deleted_result.reason)
    else:
        add_step("OCR Validation Passed: OneDrive File Explorer is open and the RunnerEvidence folder is not visible after deletion")


def _validate_fslogix_profile_log_evidence_screenshots(
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    fslogix_path = _latest_evidence_path(evidence_paths, "fslogix_profile_log")
    if fslogix_path is None:
        raise EvidenceValidationFailed("FSLogix Profile log evidence screenshot was not captured.")

    add_step("OCR validation started for FSLogix Profile log evidence screenshot")
    result = validate_fslogix_profile_log_with_windows_ocr(fslogix_path)
    if not result.valid:
        ai_result = _try_ai_fallback(
            fslogix_path,
            raw_config,
            "Shakedown_FSLogix_Profile_Log_Evidence",
            "FSLogix Profile log evidence screenshot",
            "fslogix_profile_log",
            add_step,
            result,
        )
        if ai_result is None:
            add_step(f"FSLogix Profile log validation failed: {result.reason}", "ERROR")
            raise EvidenceValidationFailed(result.reason)
    else:
        add_step('OCR Validation Passed: FSLogix log is open in Notepad/Text Editor and "copy failure" search is visible')


def _validate_temp_folder_evidence_screenshots(
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    temp_path = _latest_evidence_path(evidence_paths, "temp_files")
    if temp_path is None:
        raise EvidenceValidationFailed("TEMP folder evidence screenshot was not captured.")

    add_step("OCR validation started for TEMP folder evidence screenshot")
    result = validate_temp_folder_with_windows_ocr(temp_path)
    if not result.valid:
        ai_result = _try_ai_fallback(
            temp_path,
            raw_config,
            "Shakedown_Temp_Folder_Evidence",
            "TEMP folder evidence screenshot",
            "temp_folder",
            add_step,
            result,
        )
        if ai_result is None:
            add_step(f"TEMP folder validation failed: {result.reason}", "ERROR")
            raise EvidenceValidationFailed(result.reason)
    else:
        add_step("OCR Validation Passed: TEMP folder is open in focused File Explorer")


def _record_applist_evidence_metadata(
    screenshot_path: Path,
    context: AutomationContext,
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    context.metadata["applist_search_text"] = "NOT OK"
    context.metadata["applist_search_evidence_captured"] = False

    add_step("OCR validation started for Applist evidence screenshot")
    ocr_result = validate_applist_evidence_with_windows_ocr(screenshot_path)
    if not ocr_result.valid:
        ai_result = _try_ai_fallback(
            screenshot_path,
            raw_config,
            "Applist_Validation_Evidence",
            "Applist evidence screenshot",
            "applist",
            add_step,
            ocr_result,
        )
        if ai_result is None:
            context.metadata["applist_validation_error"] = ocr_result.reason
            add_step(f"Applist validation failed: {ocr_result.reason}", "ERROR")
            raise EvidenceValidationFailed(ocr_result.reason)
        context.metadata["applist_search_evidence_captured"] = True
        add_step('Applist validation recovered through AI fallback: file is open and "NOT OK" search is visible')
    else:
        context.metadata["applist_search_evidence_captured"] = True
        add_step('OCR Validation Passed: Applist file is open and "NOT OK" search is visible')

    try:
        not_ok_found = _detect_highlighted_applist_search_result(screenshot_path)
    except OSError as exc:
        not_ok_found = False
        context.metadata["applist_visual_detection_error"] = str(exc)
        add_step(f"Applist visual check could not inspect screenshot: {exc}", "WARNING")

    context.metadata["applist_not_ok_found"] = not_ok_found
    add_step("Applist validation evidence captured: Notepad search for NOT OK completed.")
    if not_ok_found:
        add_step('Applist result: highlighted "NOT OK" occurrence detected in the file.')
    else:
        add_step('Applist result: no highlighted "NOT OK" occurrence detected; report wording remains unchanged.')


def _validate_iat_core_application_evidence_screenshots(
    context: AutomationContext,
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    iat_validations = (
        (
            "7-Zip",
            "7-zip_evidence",
            "7-zip",
            "seven_zip",
            "iat_7zip",
            validate_7zip_programs_and_features_with_windows_ocr,
        ),
        (
            "Adobe Acrobat Reader",
            "adobe_acrobat_evidence",
            "Adobe Acrobat Reader",
            "adobe_acrobat",
            "iat_adobe",
            validate_adobe_acrobat_programs_and_features_with_windows_ocr,
        ),
        (
            "Microsoft Office",
            "Microsoft_Office_evidence",
            "apps",
            "microsoft_office",
            "iat_office",
            validate_microsoft_office_programs_and_features_with_windows_ocr,
        ),
        (
            "Microsoft Visio",
            "Microsoft_Visio_evidence",
            "visio",
            "microsoft_visio",
            "iat_visio",
            validate_microsoft_visio_programs_and_features_with_windows_ocr,
        ),
        (
            "Microsoft Project",
            "Microsoft_Project_evidence",
            "project",
            "microsoft_project",
            "iat_project",
            validate_microsoft_project_programs_and_features_with_windows_ocr,
        ),
        (
            "OpenJDK / JRE",
            "OpenJDK_JRE_evidence",
            "JRE",
            "openjdk_jre",
            "iat_openjdk",
            validate_openjdk_jre_programs_and_features_with_windows_ocr,
        ),
        (
            "FSLogix",
            "fslogix_apps_evidence",
            "fslogix",
            "fslogix_apps",
            "iat_fslogix",
            validate_fslogix_apps_programs_and_features_with_windows_ocr,
        ),
    )

    for display_name, evidence_prefix, search_term, metadata_prefix, description_key, validator in iat_validations:
        evidence_path = _latest_evidence_path(context.evidence_paths, evidence_prefix)
        if evidence_path is None:
            raise EvidenceValidationFailed(f"{display_name} evidence screenshot was not captured.")

        context.metadata[f"{metadata_prefix}_search_term"] = search_term
        context.metadata[f"{metadata_prefix}_available"] = False
        context.metadata[f"{metadata_prefix}_version"] = ""

        add_step(f"OCR validation started for {display_name} Programs and Features evidence screenshot")
        result = validator(evidence_path)
        if not result.valid:
            ai_result = _try_ai_fallback(
                evidence_path,
                raw_config,
                "IAT_Core_Application_Test_Evidence",
                f"{display_name} Programs and Features evidence screenshot",
                description_key,
                add_step,
                result,
            )
            if ai_result is None:
                context.metadata[f"{metadata_prefix}_validation_error"] = result.reason
                add_step(f"{display_name} validation failed: {result.reason}", "ERROR")
                raise EvidenceValidationFailed(result.reason)
            ai_version = getattr(ai_result, "version", "") or ""
            ai_available = getattr(ai_result, "available", None)
            context.metadata[f"{metadata_prefix}_available"] = (
                bool(ai_available) if ai_available is not None else bool(ai_version)
            )
            context.metadata[f"{metadata_prefix}_version"] = ai_version
            add_step(f"{display_name} validation recovered through AI fallback")
            continue

        if result.version:
            context.metadata[f"{metadata_prefix}_available"] = True
            context.metadata[f"{metadata_prefix}_version"] = result.version
            add_step(f"OCR Validation Passed: {result.reason}")
        else:
            add_step(f"OCR Validation Passed: {result.reason}")


def _detect_highlighted_applist_search_result(screenshot_path: Path) -> bool:
    with Image.open(screenshot_path) as image:
        rgb_image = image.convert("RGB")

    width, height = rgb_image.size
    y_start = max(190, int(height * 0.18))
    y_end = max(y_start + 1, height - 85)
    crop = rgb_image.crop((0, y_start, width, y_end))

    max_blue_run, blue_rows = _highlight_run_stats(crop, _is_blue_selection_pixel)
    max_yellow_run, yellow_rows = _highlight_run_stats(crop, _is_yellow_highlight_pixel)
    return (max_blue_run >= 18 and blue_rows >= 5) or (max_yellow_run >= 18 and yellow_rows >= 5)


def _highlight_run_stats(image: Image.Image, predicate: Callable[[int, int, int], bool]) -> tuple[int, int]:
    width, height = image.size
    max_run = 0
    qualifying_rows = 0
    for y in range(height):
        current_run = 0
        row_max = 0
        for x in range(width):
            red, green, blue = image.getpixel((x, y))
            if predicate(red, green, blue):
                current_run += 1
                row_max = max(row_max, current_run)
            else:
                current_run = 0
        if row_max >= 18:
            qualifying_rows += 1
        max_run = max(max_run, row_max)
    return max_run, qualifying_rows


def _is_blue_selection_pixel(red: int, green: int, blue: int) -> bool:
    return blue >= 90 and red <= 180 and blue - red >= 10 and blue >= green - 5


def _is_yellow_highlight_pixel(red: int, green: int, blue: int) -> bool:
    return red >= 200 and green >= 175 and blue <= 120 and abs(red - green) <= 90


def _validate_office_evidence_screenshots(
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    office_evidence = (
        ("PowerPoint", "powerpnt_evidence", "office_about_powerpoint"),
    )
    for product_name, prefix, description_key in office_evidence:
        evidence_path = _latest_evidence_path(evidence_paths, prefix)
        if evidence_path is None:
            raise EvidenceValidationFailed(f"{product_name} About dialog evidence screenshot was not captured.")

        add_step(f"OCR validation started for {product_name} About dialog evidence")
        result = validate_office_about_with_windows_ocr(evidence_path, product_name)
        if not result.valid:
            ai_result = _try_ai_fallback(
                evidence_path,
                raw_config,
                "Office_Applications_Launch",
                f"{product_name} About dialog evidence",
                description_key,
                add_step,
                result,
            )
            if ai_result is None:
                add_step(f"{product_name} Office About validation failed: {result.reason}", "ERROR")
                raise EvidenceValidationFailed(result.reason)
            ai_session_id = _office_ai_field(ai_result, "session_id")
            if not ai_session_id:
                add_step(
                    f"{product_name} Office About AI fallback did not return mandatory Session ID.",
                    "ERROR",
                )
                raise EvidenceValidationFailed(
                    f"{product_name} About dialog evidence is missing mandatory Session ID."
                )
            add_step(f"{product_name} Office About validation recovered through AI fallback")
            continue

        if result.license_id:
            add_step(
                "OCR Validation Passed: "
                f"{product_name} About dialog shows Session ID {result.session_id} "
                f"and License ID {result.license_id}"
            )
        else:
            add_step(
                "OCR Validation Passed: "
                f"{product_name} About dialog shows mandatory Session ID {result.session_id}; "
                "License ID not visible/available"
            )


def _remove_retry_evidence_files(evidence_paths: list[Path], add_step: Callable[..., None]) -> None:
    for path in tuple(evidence_paths):
        try:
            path.unlink(missing_ok=True)
            add_step(f"Removed invalid retry evidence screenshot: {path}")
        except OSError as exc:
            add_step(f"Unable to remove invalid retry evidence screenshot {path}: {exc}", "ERROR")
    evidence_paths.clear()


def _preserve_retry_evidence_files(evidence_paths: list[Path], add_step: Callable[..., None]) -> list[Path]:
    preserved_paths: list[Path] = []
    for path in tuple(evidence_paths):
        if not path.exists():
            continue
        target_path = _validation_failure_target_path(path)
        try:
            if target_path == path:
                preserved_paths.append(path)
                add_step(f"Retaining failed evidence screenshot: {path}")
            else:
                path.replace(target_path)
                preserved_paths.append(target_path)
                add_step(f"Preserved failed evidence screenshot: {target_path}")
        except OSError as exc:
            add_step(f"Unable to preserve failed evidence screenshot {path}: {exc}", "ERROR")
    evidence_paths.clear()
    return preserved_paths


def _delete_retained_failure_evidence_files(paths: list[Path], add_step: Callable[..., None]) -> None:
    for path in tuple(paths):
        try:
            path.unlink(missing_ok=True)
            add_step(f"Removed superseded failed evidence screenshot: {path}")
        except OSError as exc:
            add_step(f"Unable to remove superseded failed evidence screenshot {path}: {exc}", "ERROR")
    paths.clear()


def _cleanup_before_validation_retry(
    test_case_name: str,
    context: AutomationContext,
    config: AppConfig,
    add_step: Callable[..., None],
) -> None:
    if not _should_validate_silo43_oracle_12_bin_path_evidence(test_case_name):
        return
    add_step("Cleanup before Oracle PATH retry: close previous Command Prompt window", "WARNING")
    try:
        context.press("esc")
        context.hotkey("alt", "f4")
        context.wait(config.wait("silo43_oracle_retry_cleanup_wait_sec", 2.0))
    except StopRequested:
        raise
    except BaseException as exc:
        add_step(f"Oracle PATH retry cleanup warning: {exc}", "WARNING")


def _validation_failure_target_path(path: Path) -> Path:
    if "_Pass_" in path.name:
        candidate = path.with_name(path.name.replace("_Pass_", "_Fail_", 1))
    elif "_Fail_" in path.name:
        candidate = path
    else:
        candidate = path.with_name(f"{path.stem}_Fail{path.suffix}")

    if candidate == path or not candidate.exists():
        return candidate

    index = 2
    while True:
        retry_candidate = candidate.with_name(f"{candidate.stem}_{index}{candidate.suffix}")
        if not retry_candidate.exists():
            return retry_candidate
        index += 1


def _latest_evidence_path(evidence_paths: list[Path], prefix: str) -> Path | None:
    target_root = prefix.casefold()
    for path in reversed(evidence_paths):
        if _evidence_name_root(path) == target_root and path.exists():
            return path
    return None


def _latest_valid_silo43_oracle_path_evidence(
    evidence_paths: list[Path],
    add_step: Callable[..., None],
) -> Path | None:
    target_root = "silo43_oracle_12_bin_path_evidence"
    for path in reversed(evidence_paths):
        if _evidence_name_root(path) != target_root or not path.exists():
            continue
        if "_Pass_" not in path.name:
            add_step(f"Skipping non-pass Oracle PATH screenshot for NICE validation: {path}", "WARNING")
            continue
        oracle_result = validate_silo43_oracle_12_bin_path_with_windows_ocr(path)
        if oracle_result.valid:
            add_step(f"Using validated Oracle PATH screenshot for NICE validation: {path}")
            return path
        add_step(
            "Skipping Oracle PATH screenshot for NICE validation because Oracle validation failed: "
            f"{oracle_result.reason}",
            "WARNING",
        )
    return None


def _evidence_name_root(path: Path) -> str:
    name_lower = path.name.casefold()
    for marker in ("_pass_", "_fail_"):
        marker_index = name_lower.find(marker)
        if marker_index != -1:
            return name_lower[:marker_index]
    return path.stem.casefold()


def _office_ai_field(ai_result: object, field_name: str) -> str:
    fields = getattr(ai_result, "fields", {}) or {}
    if not isinstance(fields, dict):
        return ""
    for key, value in fields.items():
        normalized_key = "".join(char for char in str(key).casefold() if char.isalnum())
        if normalized_key == "".join(char for char in field_name.casefold() if char.isalnum()):
            return str(value or "").strip()
    return ""


def _validate_edge_browser_pass_screenshot(
    screenshot_path: Path,
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    add_step("OCR validation started for Edge browser evidence screenshot")
    result = validate_edge_browser_version_with_windows_ocr(screenshot_path)
    if result.valid:
        add_step(f"OCR Validation Passed: Microsoft Edge version {result.version} is visible")
        return

    add_step(f"Edge browser validation failed: {result.reason}", "ERROR")
    ai_result = _try_ai_fallback(
        screenshot_path,
        raw_config,
        "Edge_Browser_Version_Evidence",
        "Edge browser evidence screenshot",
        "edge_browser_registry",
        add_step,
        result,
    )
    if ai_result is not None:
        return
    try:
        screenshot_path.unlink(missing_ok=True)
    except OSError as exc:
        add_step(f"Unable to remove invalid pass screenshot: {exc}", "ERROR")
    try:
        evidence_paths.remove(screenshot_path)
    except ValueError:
        pass
    raise EvidenceValidationFailed(result.reason)


def _validate_zscaler_pass_screenshot(
    screenshot_path: Path,
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    add_step("OCR validation started for Zscaler evidence screenshot")
    result = validate_zscaler_services_with_windows_ocr(screenshot_path)
    if result.valid:
        add_step("OCR Validation Passed: ZCCVDI Service Status is ON and Authentication Status is Authenticated")
        return

    add_step(f"Zscaler validation failed: {result.reason}", "ERROR")
    ai_result = _try_ai_fallback(
        screenshot_path,
        raw_config,
        "Zscaler_Services_Evidence",
        "Zscaler evidence screenshot",
        "zscaler",
        add_step,
        result,
    )
    if ai_result is not None:
        return
    try:
        screenshot_path.unlink(missing_ok=True)
    except OSError as exc:
        add_step(f"Unable to remove invalid pass screenshot: {exc}", "ERROR")
    try:
        evidence_paths.remove(screenshot_path)
    except ValueError:
        pass
    raise EvidenceValidationFailed(result.reason)


def _validate_desktop_availability_pass_screenshot(
    screenshot_path: Path,
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    add_step("OCR validation started for desktop availability evidence screenshot")
    result = validate_desktop_availability_with_windows_ocr(screenshot_path)
    if result.valid:
        add_step("OCR Validation Passed: desktop is visible and no foreground application text was detected")
        return

    add_step(f"Desktop availability validation failed: {result.reason}", "ERROR")
    ai_result = _try_ai_fallback(
        screenshot_path,
        raw_config,
        "Shakedown_Desktop_Availability_Evidence",
        "desktop availability evidence screenshot",
        "desktop_availability",
        add_step,
        result,
    )
    if ai_result is not None:
        return
    try:
        screenshot_path.unlink(missing_ok=True)
    except OSError as exc:
        add_step(f"Unable to remove invalid pass screenshot: {exc}", "ERROR")
    try:
        evidence_paths.remove(screenshot_path)
    except ValueError:
        pass
    raise EvidenceValidationFailed(result.reason)


def _validate_onedrive_sync_pass_screenshot(
    screenshot_path: Path,
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    add_step("OCR validation started for OneDrive sync evidence screenshot")
    result = validate_onedrive_sync_with_windows_ocr(screenshot_path)
    if result.valid:
        add_step("OCR Validation Passed: OneDrive folder is open and File Explorer is in focus")
        return

    add_step(f"OneDrive sync validation failed: {result.reason}", "ERROR")
    ai_result = _try_ai_fallback(
        screenshot_path,
        raw_config,
        "Shakedown_OneDrive_Sync_Evidence",
        "OneDrive sync evidence screenshot",
        "onedrive_sync",
        add_step,
        result,
    )
    if ai_result is not None:
        return
    try:
        screenshot_path.unlink(missing_ok=True)
    except OSError as exc:
        add_step(f"Unable to remove invalid pass screenshot: {exc}", "ERROR")
    try:
        evidence_paths.remove(screenshot_path)
    except ValueError:
        pass
    raise EvidenceValidationFailed(result.reason)


def _validate_windows_version_pass_screenshot(
    screenshot_path: Path,
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    add_step("OCR validation started for Windows version evidence screenshot")
    result = validate_windows_version_with_windows_ocr(screenshot_path)
    if result.valid:
        add_step(f"OCR Validation Passed: About Windows is visible and version {result.version} was read")
        return

    add_step(f"Windows version validation failed: {result.reason}", "ERROR")
    ai_result = _try_ai_fallback(
        screenshot_path,
        raw_config,
        "Shakedown_Windows_Version_Evidence",
        "Windows version evidence screenshot",
        "windows_version",
        add_step,
        result,
    )
    if ai_result is not None:
        return
    try:
        screenshot_path.unlink(missing_ok=True)
    except OSError as exc:
        add_step(f"Unable to remove invalid pass screenshot: {exc}", "ERROR")
    try:
        evidence_paths.remove(screenshot_path)
    except ValueError:
        pass
    raise EvidenceValidationFailed(result.reason)


def _validate_webview_pass_screenshot(
    screenshot_path: Path,
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    add_step("OCR validation started for Edge WebView evidence screenshot")
    result = validate_webview_version_with_windows_ocr(screenshot_path)
    if result.valid:
        add_step(f"OCR Validation Passed: WebView2 Runtime version {result.version} is visible")
        return

    add_step(f"WebView validation failed: {result.reason}", "ERROR")
    ai_result = _try_ai_fallback(
        screenshot_path,
        raw_config,
        "Edge_WebView_Version_Evidence",
        "Edge WebView evidence screenshot",
        "webview_registry",
        add_step,
        result,
    )
    if ai_result is not None:
        return
    try:
        screenshot_path.unlink(missing_ok=True)
    except OSError as exc:
        add_step(f"Unable to remove invalid pass screenshot: {exc}", "ERROR")
    try:
        evidence_paths.remove(screenshot_path)
    except ValueError:
        pass
    raise EvidenceValidationFailed(result.reason)


def _validate_silo43_oracle_12_bin_path_pass_screenshot(
    screenshot_path: Path,
    evidence_paths: list[Path],
    add_step: Callable[..., None],
) -> None:
    add_step("OCR validation started for Silo 43 Oracle 12 bin PATH evidence screenshot")
    result = validate_silo43_oracle_12_bin_path_with_windows_ocr(screenshot_path)
    if result.valid:
        add_step("OCR Validation Passed: Oracle 12 client_32 bin path is the first PATH entry")
        return

    add_step(f"Silo 43 Oracle 12 bin PATH validation failed: {result.reason}", "ERROR")
    _log_ocr_failure_details(result, add_step, label="Silo 43 Oracle PATH OCR raw text")
    try:
        screenshot_path.unlink(missing_ok=True)
    except OSError as exc:
        add_step(f"Unable to remove invalid pass screenshot: {exc}", "ERROR")
    try:
        evidence_paths.remove(screenshot_path)
    except ValueError:
        pass
    raise EvidenceValidationFailed(result.reason)


def _validate_silo43_nice_env_variables_screenshot(
    evidence_paths: list[Path],
    add_step: Callable[..., None],
) -> None:
    screenshot_path = _latest_valid_silo43_oracle_path_evidence(evidence_paths, add_step)
    if screenshot_path is None:
        raise EvidenceValidationFailed(
            "A valid Oracle 12 PATH evidence screenshot was not available for NICE environment validation."
        )

    add_step("OCR validation started for Silo 43 NICE environment variable evidence")
    result = validate_silo43_nice_env_variables_with_windows_ocr(screenshot_path)
    if result.valid:
        add_step(
            "OCR Validation Passed: NICE Player Codec Pack and NICE Player Release 6 are the second and third PATH entries"
        )
        return

    add_step(f"Silo 43 NICE environment variable validation failed: {result.reason}", "ERROR")
    _log_ocr_failure_details(result, add_step, label="Silo 43 NICE PATH OCR raw text")
    raise EvidenceValidationFailed(result.reason)


def _validate_silo43_vls_privilege_warning_screenshot(
    evidence_paths: list[Path],
    add_step: Callable[..., None],
) -> None:
    screenshot_path = _latest_evidence_path(evidence_paths, "silo43_vls_privilege_warning_evidence")
    if screenshot_path is None:
        raise EvidenceValidationFailed("Silo 43 VLS privilege warning screenshot was not captured.")

    add_step("OCR validation started for Silo 43 VLS privilege warning evidence")
    result = validate_silo43_vls_privilege_warning_with_windows_ocr(screenshot_path)
    if result.valid:
        add_step("OCR Validation Passed: VLS APPLICATION PRIVILIGE Help Desk warning popup is visible")
        return

    add_step(f"Silo 43 VLS privilege warning validation failed: {result.reason}", "ERROR")
    _log_ocr_failure_details(result, add_step, label="Silo 43 VLS warning OCR raw text")
    raise EvidenceValidationFailed(result.reason)


def _validate_silo43_ping_prod_dvfs_pass_screenshot(
    screenshot_path: Path,
    evidence_paths: list[Path],
    add_step: Callable[..., None],
) -> None:
    add_step("OCR validation started for Silo 43 prod.dvfs.com ping evidence screenshot")
    result = validate_silo43_ping_prod_dvfs_with_windows_ocr(screenshot_path)
    if result.valid:
        add_step("OCR Validation Passed: ping prod.dvfs.com shows replies and 0% packet loss")
        return

    add_step(f"Silo 43 prod.dvfs.com ping validation failed: {result.reason}", "ERROR")
    _log_ocr_failure_details(result, add_step, label="Silo 43 ping OCR raw text")
    try:
        screenshot_path.unlink(missing_ok=True)
    except OSError as exc:
        add_step(f"Unable to remove invalid pass screenshot: {exc}", "ERROR")
    try:
        evidence_paths.remove(screenshot_path)
    except ValueError:
        pass
    raise EvidenceValidationFailed(result.reason)


def _validate_silo43_bad_folder_screenshot(
    evidence_paths: list[Path],
    add_step: Callable[..., None],
) -> None:
    screenshot_path = _latest_evidence_path(evidence_paths, "silo43_bad_folder_evidence")
    if screenshot_path is None:
        raise EvidenceValidationFailed("Silo 43 BAD folder screenshot was not captured.")

    add_step("OCR validation started for Silo 43 BAD folder evidence")
    result = validate_silo43_bad_folder_with_windows_ocr(screenshot_path)
    if result.valid:
        add_step("OCR Validation Passed: C:\\BAD is open and a 2026 modified date is visible")
        return

    add_step(f"Silo 43 BAD folder validation failed: {result.reason}", "ERROR")
    _log_ocr_failure_details(result, add_step, label="Silo 43 BAD folder OCR raw text")
    raise EvidenceValidationFailed(result.reason)


def _validate_webview_context_screenshots(
    evidence_paths: list[Path],
    add_step: Callable[..., None],
    raw_config: dict,
) -> None:
    webview_path = _latest_evidence_path(evidence_paths, "webview_evidence")
    if webview_path is None:
        raise EvidenceValidationFailed("Edge WebView evidence screenshot was not captured.")

    _validate_webview_pass_screenshot(webview_path, evidence_paths, add_step, raw_config)

    edge_browser_path = _latest_evidence_path(evidence_paths, "edge_evidence")
    if edge_browser_path is not None:
        _validate_edge_browser_pass_screenshot(edge_browser_path, evidence_paths, add_step, raw_config)


def _log_ai_validation_pass(result, add_step: Callable[..., None]) -> None:
    ipv4_summary = ", ".join(result.ipv4_addresses) if result.ipv4_addresses else "detected"
    add_step(
        "AI Validation Passed: "
        f"hostname={result.cmd_hostname or result.overlay_hostname or 'detected'}, "
        f"IPv4={ipv4_summary}"
    )
