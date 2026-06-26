from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable

from core.automation_context import AutomationContext
from core.config import AppConfig
from core.edge_sync_detection import find_sign_in_button_target
from core.evidence_replacement import remove_existing_evidence_for_prefixes, remove_existing_evidence_for_test_case
from core.execution_log import ExecutionLog, desktop_scoped_path
from core.runner import (
    EDGE_BROWSER_TEST_NAME,
    EDGE_WEBVIEW_TEST_NAME,
    AIValidationFailed,
    EvidenceValidationFailed,
    ExecutionResult,
    TestRunner,
    _record_applist_evidence_metadata,
    _validate_edge_browser_pass_screenshot,
    _validate_hostname_ip_pass_screenshot,
    _validate_policy_pac_evidence_screenshots,
    _validate_shakedown_edge_sync_evidence_screenshots,
    _validate_webview_pass_screenshot,
)
from core.screenshot import ScreenshotManager
from core.skip_control import CombinedStopSkipEvent, consume_skip_request
from core.stop_control import StopRequested, interruptible_sleep, wait_if_paused
from core.test_categories import (
    IAT_EVIDENCE_FOLDER,
    IAT_TEST_CASE_ORDER,
    MANDATORY_EVIDENCE_FOLDER,
    APPLIST_TEST_CASE_NAME,
    POST_COMPLETE_ZSCALER_TEST_NAME,
    SHAKEDOWN_EVIDENCE_FOLDER,
    SHAKEDOWN_TEST_CASE_ORDER,
    SILO43_EVIDENCE_FOLDER,
    SILO43_TEST_CASE_ORDER,
    is_ring0_desktop,
    is_silo43_desktop,
    is_success_status,
    mandatory_order_for_desktop,
)
from core.test_loader import TestCase
from core.word_report import generate_complete_testing_report
from core.ocr_validation import validate_zscaler_services_with_windows_ocr
from core.zscaler_recovery import (
    recover_zscaler_connection_if_needed,
    zscaler_healthy_state_visible,
    zscaler_problem_state_visible,
)


@dataclass(frozen=True)
class MasterExecutionResult:
    status: str
    log_path: Path
    failed_count: int
    manual_check_required: bool = False
    manual_check_message: str | None = None
    passed_count: int = 0
    total_count: int = 0
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class CompleteExecutionResult:
    status: str
    log_path: Path
    failed_count: int
    mandatory_status: str
    shakedown_status: str
    iat_status: str
    silo43_status: str = "Skipped"
    report_path: Path | None = None
    manual_check_required: bool = False
    manual_check_message: str | None = None
    passed_count: int = 0
    total_count: int = 0
    duration_seconds: float = 0.0


@dataclass
class MandatoryCommandBlockResult:
    entries: list[dict]
    completed_test_names: set[str]
    stopped: bool = False
    manual_check_required: bool = False
    manual_check_message: str | None = None


@dataclass
class ShakedownEdgeBlockResult:
    entries: list[dict]
    completed_test_names: set[str]
    stopped: bool = False


COMMAND_SESSION_TESTS = (
    "Hostname_and_IP_Evidence",
    EDGE_WEBVIEW_TEST_NAME,
    EDGE_BROWSER_TEST_NAME,
    APPLIST_TEST_CASE_NAME,
)

SHAKEDOWN_EDGE_SYNC_TEST_NAME = "Shakedown_Edge_Sync_Evidence"
SHAKEDOWN_EDGE_POLICY_PAC_TEST_NAME = "Shakedown_Edge_Policy_PAC_Evidence"
SHAKEDOWN_EDGE_SESSION_TESTS = (
    SHAKEDOWN_EDGE_SYNC_TEST_NAME,
    SHAKEDOWN_EDGE_POLICY_PAC_TEST_NAME,
)
SHAKEDOWN_EDGE_SEARCH_TEXT = "Apps: Microsoft Edge"
SHAKEDOWN_EDGE_POLICY_URL = "edge://policy"

WEBVIEW_REGISTRY_COMMAND = (
    "Get-itemproperty -path "
    "'HKLM:\\software\\Wow6432Node\\Microsoft\\EdgeUpdate\\Clients\\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'"
)

EDGE_BROWSER_REGISTRY_COMMAND = (
    "Get-itemproperty -path "
    "'HKLM:\\software\\Wow6432Node\\Microsoft\\EdgeUpdate\\Clients\\{56EB18F8-B008-4CBD-B6D2-8C97FE7E9062}'"
)

APPLIST_OPEN_LATEST_COMMAND = (
    "notepad ((Get-ChildItem 'C:\\Temp\\Applist*' -File | "
    "Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName)"
)


class MasterRunner:
    def __init__(
        self,
        config: AppConfig,
        citrix_desktop_name: str,
        status_callback: Callable[[str], None] | None = None,
        test_status_callback: Callable[[str, str], None] | None = None,
        manual_confirmation_callback: Callable[[ExecutionResult], None] | None = None,
        stop_event: Event | None = None,
        pause_event: Event | None = None,
        skip_event: Event | None = None,
    ) -> None:
        self.config = config
        self.citrix_desktop_name = citrix_desktop_name
        self.status_callback = status_callback or (lambda message: None)
        self.test_status_callback = test_status_callback or (lambda test_id, status: None)
        self.manual_confirmation_callback = manual_confirmation_callback
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.skip_event = skip_event
        self.master_steps: list[dict[str, str]] = []
        self.cleanup_timings: list[dict[str, object]] = []
        self.optimization_notes: list[dict[str, object]] = []

    def run(self, test_cases: list[TestCase]) -> MasterExecutionResult:
        started_at = datetime.now()
        tests_by_name = {test_case.name: test_case for test_case in test_cases}
        mandatory_order = mandatory_order_for_desktop(self.citrix_desktop_name)
        results = []
        stopped = False
        manual_check_required = False
        manual_check_message = None
        combined_edge_browser_result: ExecutionResult | None = None
        command_block_completed_tests: set[str] = set()

        self._message("Starting Mandatory Testcases")
        self._message(f"Citrix Desktop Name: {self.citrix_desktop_name}")
        if APPLIST_TEST_CASE_NAME not in mandatory_order:
            self._message("Ring0 desktop detected. Applist validation will be skipped.")
        self._record_mandatory_optimization_notes(mandatory_order)

        try:
            for index, test_name in enumerate(mandatory_order):
                self._check_stop()
                if test_name in command_block_completed_tests:
                    self._message(f"Mandatory sequence already completed by optimized command block: {test_name}")
                    continue

                test_case = tests_by_name.get(test_name)
                if test_case is None:
                    self._message(f"Missing mandatory test case: {test_name}", "ERROR")
                    results.append(
                        {
                            "test_case": test_name,
                            "status": "Fail",
                            "error": "Test case was not found in the GUI test list.",
                            "screenshots": [],
                            "log_path": None,
                        }
                    )
                    continue

                if self._should_run_mandatory_command_session_block(mandatory_order, index, tests_by_name):
                    block_result = self._run_mandatory_command_session_block(mandatory_order, tests_by_name)
                    results.extend(block_result.entries)
                    command_block_completed_tests.update(block_result.completed_test_names)
                    if block_result.manual_check_required:
                        manual_check_required = True
                        manual_check_message = block_result.manual_check_message
                        break
                    if block_result.stopped:
                        stopped = True
                        self._message("Mandatory Testcases stopped by user", "ERROR")
                        break
                    if index < len(mandatory_order) - 1:
                        delay = self.config.wait("mandatory_between_tests_wait_sec", 2.0)
                        self._message(f"Mandatory delay confirmed before next test: {delay} second(s)")
                        _sleep(delay, self.stop_event, self.pause_event)
                    continue

                if self._consume_skip_request():
                    self._message(f"Skip requested before {test_case.name}; marking testcase as Skipped.")
                    self.test_status_callback(test_case.id, "Skipped")
                    results.append(self._skipped_log_entry(test_case.name))
                    continue

                if test_case.name == EDGE_BROWSER_TEST_NAME and combined_edge_browser_result is not None:
                    self.test_status_callback(test_case.id, "Running")
                    self._message(f"Mandatory sequence running: {test_case.name}")
                    self._message(
                        "Edge browser evidence already captured in the combined Edge registry session; "
                        "skipping separate Command Prompt launch."
                    )
                    self.test_status_callback(test_case.id, "Pass")
                    results.append(self._combined_edge_browser_log_entry(combined_edge_browser_result))
                    if index < len(mandatory_order) - 1:
                        delay = self.config.wait("mandatory_between_tests_wait_sec", 2.0)
                        self._message(f"Mandatory delay confirmed before next test: {delay} second(s)")
                        _sleep(delay, self.stop_event, self.pause_event)
                    continue

                self.test_status_callback(test_case.id, "Running")
                self._message(f"Mandatory sequence running: {test_case.name}")
                runtime_metadata = {}
                if self._should_combine_edge_registry(mandatory_order, index):
                    runtime_metadata["combine_edge_registry_evidence"] = True
                result = TestRunner(
                    config=self.config,
                    citrix_desktop_name=self.citrix_desktop_name,
                    status_callback=self.status_callback,
                    stop_event=self._runner_stop_event(),
                    pause_event=self.pause_event,
                    runtime_metadata=runtime_metadata,
                ).run(test_case)

                if result.status == "Stopped" and self._consume_skip_request():
                    self._message(f"Skip requested for {test_case.name}; marking testcase as Skipped and continuing.")
                    self.test_status_callback(test_case.id, "Skipped")
                    results.append(self._skipped_log_entry(test_case.name, result))
                    self._cleanup_after_test(test_case.name)
                    if index < len(mandatory_order) - 1:
                        delay = self.config.wait("mandatory_between_tests_wait_sec", 2.0)
                        self._message(f"Mandatory delay confirmed before next test: {delay} second(s)")
                        _sleep(delay, self.stop_event, self.pause_event)
                    continue

                self.test_status_callback(test_case.id, result.status)

                results.append(self._result_to_log_entry(result))
                if test_case.name == EDGE_WEBVIEW_TEST_NAME:
                    if result.status == "Pass" and result.metadata.get("combined_edge_registry_evidence"):
                        combined_edge_browser_result = result
                    else:
                        combined_edge_browser_result = None
                if result.manual_confirmation_required:
                    self._pause_for_manual_confirmation(result, "Mandatory Testcases")

                if result.requires_manual_check:
                    manual_check_required = True
                    manual_check_message = result.manual_check_message or result.error_message
                    self._message(
                        manual_check_message
                        or "Manual check required before continuing Mandatory Testcases.",
                        "ERROR",
                    )
                    self._message(
                        "Mandatory sequence stopped because Hostname_and_IP_Evidence needs manual review.",
                        "ERROR",
                    )
                    break

                if result.status == "Stopped":
                    stopped = True
                    self._message("Mandatory Testcases stopped by user", "ERROR")
                    break

                self._check_stop()
                self._cleanup_after_test(test_case.name)

                if index < len(mandatory_order) - 1:
                    if test_case.name == EDGE_WEBVIEW_TEST_NAME and combined_edge_browser_result is not None:
                        continue
                    delay = self.config.wait("mandatory_between_tests_wait_sec", 2.0)
                    self._message(f"Mandatory delay confirmed before next test: {delay} second(s)")
                    _sleep(delay, self.stop_event, self.pause_event)
        except StopRequested:
            stopped = True
            self._message("Mandatory Testcases stopped by user", "ERROR")

        ended_at = datetime.now()
        failed_count = sum(1 for item in results if not is_success_status(item["status"]))
        passed_count = sum(1 for item in results if is_success_status(item["status"]))
        total_count = len(results)
        duration_seconds = round((ended_at - started_at).total_seconds(), 3)
        final_status = "Stopped" if stopped or self._is_stop_requested() else ("Pass" if failed_count == 0 else "Fail")
        log_path = self._write_master_log(started_at, ended_at, final_status, results)
        self._message(f"Mandatory Testcases completed: {final_status}")
        self._message(f"Master log: {log_path}")
        return MasterExecutionResult(
            status=final_status,
            log_path=log_path,
            failed_count=failed_count,
            manual_check_required=manual_check_required,
            manual_check_message=manual_check_message,
            passed_count=passed_count,
            total_count=total_count,
            duration_seconds=duration_seconds,
        )

    def _should_run_mandatory_command_session_block(
        self,
        mandatory_order: list[str],
        index: int,
        tests_by_name: dict[str, TestCase],
    ) -> bool:
        if mandatory_order[index] != "Hostname_and_IP_Evidence":
            return False
        settings = self.config.raw.get("optimization", {})
        if isinstance(settings, dict) and settings.get("suite_mandatory_command_session_enabled") is False:
            return False
        return len(self._mandatory_command_session_block_names(mandatory_order, tests_by_name)) >= 2

    def _mandatory_command_session_block_names(
        self,
        mandatory_order: list[str],
        tests_by_name: dict[str, TestCase],
    ) -> list[str]:
        return [
            name
            for name in COMMAND_SESSION_TESTS
            if name in mandatory_order and name in tests_by_name
        ]

    def _run_mandatory_command_session_block(
        self,
        mandatory_order: list[str],
        tests_by_name: dict[str, TestCase],
    ) -> MandatoryCommandBlockResult:
        block_names = self._mandatory_command_session_block_names(mandatory_order, tests_by_name)
        block_result = MandatoryCommandBlockResult(entries=[], completed_test_names=set())
        if not block_names:
            return block_result

        self._message("Optimized mandatory command block starting: " + " -> ".join(block_names))
        screenshots_dir = (
            desktop_scoped_path(self.config.path("screenshots_dir"), self.citrix_desktop_name)
            / MANDATORY_EVIDENCE_FOLDER
        )
        logs_dir = desktop_scoped_path(self.config.path("logs_dir"), self.citrix_desktop_name)
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        context = AutomationContext(
            config=self.config,
            log_step=lambda message, level="INFO": self._message(message, level),
            citrix_desktop_name=self.citrix_desktop_name,
            evidence_category=MANDATORY_EVIDENCE_FOLDER,
            stop_event=self.stop_event,
            pause_event=self.pause_event,
        )
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
        session_state: dict[str, object] = {
            "cmd_open": False,
            "powershell_started": False,
            "needs_cleanup": False,
            "fallback_only": False,
        }

        for test_name in block_names:
            self._check_stop()
            test_case = tests_by_name[test_name]
            if self._consume_skip_request():
                self._message(f"Skip requested before {test_case.name}; marking testcase as Skipped.")
                self.test_status_callback(test_case.id, "Skipped")
                block_result.entries.append(self._skipped_log_entry(test_case.name))
                block_result.completed_test_names.add(test_case.name)
                session_state["fallback_only"] = True
                continue

            if session_state.get("fallback_only"):
                result = self._run_mandatory_command_block_standalone(test_case)
                self._record_command_block_result(block_result, test_case, result, cleanup_after=True)
                if block_result.stopped or block_result.manual_check_required:
                    break
                continue

            try:
                result = self._run_optimized_command_session_test(
                    test_case=test_case,
                    context=context,
                    screenshots=screenshots,
                    screenshots_dir=screenshots_dir,
                    logs_dir=logs_dir,
                    block_names=block_names,
                    session_state=session_state,
                )
                self._record_command_block_result(block_result, test_case, result, cleanup_after=False)
                if block_result.stopped or block_result.manual_check_required:
                    break
            except Exception as exc:
                self._message(
                    f"Optimized command block failed during {test_case.name}: {exc}. "
                    "Falling back to the standalone testcase flow.",
                    "WARNING",
                )
                self._cleanup_optimized_command_session(session_state, test_case.name)
                session_state["fallback_only"] = True
                result = self._run_mandatory_command_block_standalone(test_case)
                self._record_command_block_result(block_result, test_case, result, cleanup_after=True)
                if block_result.stopped or block_result.manual_check_required:
                    break

        if not block_result.stopped and not block_result.manual_check_required:
            self._cleanup_optimized_command_session(session_state, None)
        self._message("Optimized mandatory command block finished")
        return block_result

    def _run_optimized_command_session_test(
        self,
        *,
        test_case: TestCase,
        context: AutomationContext,
        screenshots: ScreenshotManager,
        screenshots_dir: Path,
        logs_dir: Path,
        block_names: list[str],
        session_state: dict[str, object],
    ) -> ExecutionResult:
        self.test_status_callback(test_case.id, "Running")
        self._message(f"Mandatory command block running: {test_case.name}")
        execution_log = ExecutionLog(test_case.name, logs_dir, desktop_name=self.citrix_desktop_name)
        evidence_paths: list[Path] = []
        metadata: dict[str, object] = {
            "suite_optimized_command_session": True,
            "optimized_block": "mandatory_command_session_block",
            "optimized_block_order": list(block_names),
            "individual_testcase_run_unchanged": True,
        }
        screenshot_path: Path | None = None

        def add_step(message: str, level: str = "INFO") -> None:
            execution_log.add_step(message, level)
            self.status_callback(message)

        context.log_step = add_step
        context.evidence_paths = evidence_paths
        context.metadata = metadata

        try:
            replacement_started = time.perf_counter()
            deleted_count = remove_existing_evidence_for_test_case(screenshots_dir, test_case, add_step)
            metadata.setdefault("timings_seconds", {})["evidence_replacement_seconds"] = round(
                time.perf_counter() - replacement_started,
                3,
            )
            if deleted_count:
                add_step(f"Previous evidence replaced for this testcase: {deleted_count} screenshot(s) removed")

            add_step(f"Starting optimized command-session testcase: {test_case.name}")
            if test_case.name == "Hostname_and_IP_Evidence":
                screenshot_path = self._optimized_hostname_ip_evidence(
                    context,
                    screenshots,
                    test_case,
                    evidence_paths,
                    add_step,
                    session_state,
                )
            elif test_case.name == EDGE_WEBVIEW_TEST_NAME:
                screenshot_path = self._optimized_webview_evidence(
                    context,
                    screenshots,
                    test_case,
                    evidence_paths,
                    add_step,
                    session_state,
                )
            elif test_case.name == EDGE_BROWSER_TEST_NAME:
                screenshot_path = self._optimized_edge_browser_evidence(
                    context,
                    screenshots,
                    test_case,
                    evidence_paths,
                    add_step,
                    session_state,
                )
            elif test_case.name == APPLIST_TEST_CASE_NAME:
                screenshot_path = self._optimized_applist_evidence(
                    context,
                    screenshots,
                    test_case,
                    evidence_paths,
                    add_step,
                    session_state,
                )
            else:
                raise RuntimeError(f"Unsupported optimized command-session testcase: {test_case.name}")

            add_step("Optimized command-session testcase completed successfully")
            log_path = execution_log.finish("Pass", screenshot_path, evidence_paths, metadata)
            return ExecutionResult(
                "Pass",
                test_case.name,
                log_path,
                screenshot_path,
                tuple(evidence_paths),
                metadata=dict(metadata),
            )
        except StopRequested:
            add_step("Execution stopped by user", "ERROR")
            log_path = execution_log.finish("Stopped", screenshot_path, evidence_paths, metadata)
            return ExecutionResult("Stopped", test_case.name, log_path, screenshot_path, tuple(evidence_paths), metadata=dict(metadata))
        except Exception as exc:
            execution_log.set_error(exc)
            execution_log.finish("Fail", screenshot_path, evidence_paths, metadata)
            raise

    def _optimized_hostname_ip_evidence(
        self,
        context: AutomationContext,
        screenshots: ScreenshotManager,
        test_case: TestCase,
        evidence_paths: list[Path],
        add_step: Callable[..., None],
        session_state: dict[str, object],
    ) -> Path:
        desktop_name = (self.citrix_desktop_name or "").strip()
        if not desktop_name:
            raise RuntimeError("Please enter the Citrix Desktop Name before starting the test.")

        self._ensure_optimized_cmd_session(context, add_step, session_state)
        context.step("Optimized Step: Execute hostname command")
        context.type_text("hostname")
        context.press("enter")
        context.wait(self.config.wait("after_hostname_command_wait_sec", 2.0))

        context.step("Optimized Step: Execute ipconfig command")
        context.type_text("ipconfig")
        context.press("enter")
        context.wait(self.config.wait("after_ipconfig_enter_wait_sec", 3.0))

        context.step("Optimized Step: Capture Hostname/IP evidence")
        screenshot_path = self._capture_optimized_pass_screenshot(
            screenshots,
            context,
            evidence_paths,
            test_case.evidence_name or test_case.name,
            add_step,
        )
        _validate_hostname_ip_pass_screenshot(
            screenshot_path,
            self.config.raw,
            self.citrix_desktop_name,
            evidence_paths,
            add_step,
        )
        self._copy_optimized_pass_screenshot_if_enabled(screenshots, screenshot_path, add_step)
        return screenshot_path

    def _optimized_webview_evidence(
        self,
        context: AutomationContext,
        screenshots: ScreenshotManager,
        test_case: TestCase,
        evidence_paths: list[Path],
        add_step: Callable[..., None],
        session_state: dict[str, object],
    ) -> Path:
        self._ensure_optimized_powershell_session(context, add_step, session_state)
        self._clear_optimized_powershell(context)
        context.step(f"Optimized Step: Execute Edge WebView version command: {WEBVIEW_REGISTRY_COMMAND}")
        context.type_text(WEBVIEW_REGISTRY_COMMAND, interval=0.15)
        context.press("enter")
        context.wait(self.config.wait("webview_command_output_wait_sec", 5.0))
        context.step("Optimized Step: Capture Edge WebView evidence")
        screenshot_path = self._capture_optimized_pass_screenshot(
            screenshots,
            context,
            evidence_paths,
            test_case.evidence_name or "webview_evidence",
            add_step,
        )
        _validate_webview_pass_screenshot(screenshot_path, evidence_paths, add_step, self.config.raw)
        self._copy_optimized_pass_screenshot_if_enabled(screenshots, screenshot_path, add_step)
        return screenshot_path

    def _optimized_edge_browser_evidence(
        self,
        context: AutomationContext,
        screenshots: ScreenshotManager,
        test_case: TestCase,
        evidence_paths: list[Path],
        add_step: Callable[..., None],
        session_state: dict[str, object],
    ) -> Path:
        self._ensure_optimized_powershell_session(context, add_step, session_state)
        self._clear_optimized_powershell(context)
        context.step(f"Optimized Step: Execute Edge browser version command: {EDGE_BROWSER_REGISTRY_COMMAND}")
        context.type_text(EDGE_BROWSER_REGISTRY_COMMAND, interval=0.15)
        context.press("enter")
        context.wait(self.config.wait("edge_command_output_wait_sec", 5.0))
        context.step("Optimized Step: Capture Edge browser evidence")
        screenshot_path = self._capture_optimized_pass_screenshot(
            screenshots,
            context,
            evidence_paths,
            test_case.evidence_name or "edge_evidence",
            add_step,
        )
        _validate_edge_browser_pass_screenshot(screenshot_path, evidence_paths, add_step, self.config.raw)
        self._copy_optimized_pass_screenshot_if_enabled(screenshots, screenshot_path, add_step)
        return screenshot_path

    def _optimized_applist_evidence(
        self,
        context: AutomationContext,
        screenshots: ScreenshotManager,
        test_case: TestCase,
        evidence_paths: list[Path],
        add_step: Callable[..., None],
        session_state: dict[str, object],
    ) -> Path:
        self._ensure_optimized_powershell_session(context, add_step, session_state)
        self._clear_optimized_powershell(context)
        context.step("Optimized Step: Open newest Applist file directly from C:\\Temp")
        context.type_text(APPLIST_OPEN_LATEST_COMMAND, interval=0.15)
        context.press("enter")
        context.wait(self.config.wait("applist_open_wait_sec", 5.0))

        context.step("Optimized Step: Maximize Applist text file window")
        context.maximize_active_window()
        context.wait(self.config.wait("applist_notepad_after_maximize_wait_sec", 2.0))

        context.step("Optimized Step: Search for NOT OK inside the Applist file")
        context.hotkey("ctrl", "f")
        context.wait(self.config.wait("applist_find_dialog_wait_sec", 1.0))
        context.type_text("NOT OK", interval=0.15)
        context.press("enter")
        context.wait(self.config.wait("applist_find_result_wait_sec", 2.0))

        context.step("Optimized Step: Capture Applist validation evidence")
        screenshot_path = self._capture_optimized_pass_screenshot(
            screenshots,
            context,
            evidence_paths,
            test_case.evidence_name or "applist_evidence",
            add_step,
        )
        _record_applist_evidence_metadata(screenshot_path, context, add_step, self.config.raw)
        self._copy_optimized_pass_screenshot_if_enabled(screenshots, screenshot_path, add_step)
        session_state["active_window"] = APPLIST_TEST_CASE_NAME
        return screenshot_path

    def _ensure_optimized_cmd_session(
        self,
        context: AutomationContext,
        add_step: Callable[..., None],
        session_state: dict[str, object],
    ) -> None:
        if session_state.get("cmd_open"):
            return
        desktop_name = (self.citrix_desktop_name or "").strip()
        if not desktop_name:
            raise RuntimeError("Please enter the Citrix Desktop Name before starting the test.")

        context.step(f"Optimized Step: Activate Citrix desktop window: {desktop_name}")
        context.activate_window_by_title(
            desktop_name,
            exact=False,
            wait_after_sec=self.config.wait("citrix_activation_wait_sec", 4.0),
        )
        context.step("Optimized Step: Ensure Citrix input focus with a center-screen click")
        context.click_screen_center(wait_after_sec=self.config.wait("citrix_focus_click_wait_sec", 1.0))
        context.step("Optimized Step: Open Run dialog using Windows + R")
        context.hotkey("winleft", "r")
        context.wait(self.config.wait("run_dialog_wait_sec", 1.5))
        context.step("Optimized Step: Launch Command Prompt")
        context.type_text("cmd", interval=0.15)
        context.press("enter")
        context.wait(self.config.wait("cmd_launch_wait_sec", 3.0))
        context.step("Optimized Step: Maximize Command Prompt")
        context.hotkey("alt", "space")
        context.wait(0.5)
        context.press("x")
        context.wait(1.0)
        add_step("Optimized command session is ready")
        session_state["cmd_open"] = True
        session_state["needs_cleanup"] = True
        session_state["active_window"] = "cmd"

    def _ensure_optimized_powershell_session(
        self,
        context: AutomationContext,
        add_step: Callable[..., None],
        session_state: dict[str, object],
    ) -> None:
        self._ensure_optimized_cmd_session(context, add_step, session_state)
        if session_state.get("powershell_started"):
            return
        context.step("Optimized Step: Start PowerShell inside the existing Command Prompt")
        context.type_text("powershell", interval=0.15)
        context.press("enter")
        context.wait(2.0)
        add_step("Optimized PowerShell session is ready")
        session_state["powershell_started"] = True
        session_state["active_window"] = "powershell"

    def _clear_optimized_powershell(self, context: AutomationContext) -> None:
        context.step("Optimized Step: Clear PowerShell output")
        context.type_text("cls", interval=0.15)
        context.press("enter")
        context.wait(self.config.wait("edge_combined_clear_wait_sec", 1.0))

    def _capture_optimized_pass_screenshot(
        self,
        screenshots: ScreenshotManager,
        context: AutomationContext,
        evidence_paths: list[Path],
        evidence_name: str,
        add_step: Callable[..., None],
    ) -> Path:
        screenshots.capture_region = context.capture_region()
        screenshot_path = screenshots.capture(evidence_name, "Pass")
        evidence_paths.append(screenshot_path)
        context.evidence_paths = evidence_paths
        add_step(f"Pass screenshot saved: {screenshot_path}")
        return screenshot_path

    def _copy_optimized_pass_screenshot_if_enabled(
        self,
        screenshots: ScreenshotManager,
        screenshot_path: Path,
        add_step: Callable[..., None],
    ) -> None:
        if not self.config.screenshot_settings.get("copy_pass_screenshot_to_clipboard", True):
            return
        screenshots.copy_to_clipboard(screenshot_path)
        add_step("Pass screenshot copied to clipboard")

    def _run_mandatory_command_block_standalone(self, test_case: TestCase) -> ExecutionResult:
        self.test_status_callback(test_case.id, "Running")
        self._message(f"Mandatory command block fallback running standalone: {test_case.name}")
        return TestRunner(
            config=self.config,
            citrix_desktop_name=self.citrix_desktop_name,
            status_callback=self.status_callback,
            stop_event=self._runner_stop_event(),
            pause_event=self.pause_event,
        ).run(test_case)

    def _record_command_block_result(
        self,
        block_result: MandatoryCommandBlockResult,
        test_case: TestCase,
        result: ExecutionResult,
        *,
        cleanup_after: bool,
    ) -> None:
        if result.status == "Stopped" and self._consume_skip_request():
            self._message(f"Skip requested for {test_case.name}; marking testcase as Skipped and continuing.")
            self.test_status_callback(test_case.id, "Skipped")
            block_result.entries.append(self._skipped_log_entry(test_case.name, result))
            block_result.completed_test_names.add(test_case.name)
            if cleanup_after:
                self._cleanup_after_test(test_case.name)
            return

        self.test_status_callback(test_case.id, result.status)
        block_result.entries.append(self._result_to_log_entry(result))
        block_result.completed_test_names.add(test_case.name)

        if result.manual_confirmation_required:
            self._pause_for_manual_confirmation(result, "Mandatory Testcases")

        if result.requires_manual_check:
            block_result.manual_check_required = True
            block_result.manual_check_message = result.manual_check_message or result.error_message
            self._message(
                block_result.manual_check_message
                or "Manual check required before continuing Mandatory Testcases.",
                "ERROR",
            )
            self._message(
                "Mandatory sequence stopped because Hostname_and_IP_Evidence needs manual review.",
                "ERROR",
            )
            return

        if result.status == "Stopped":
            block_result.stopped = True
            return

        if cleanup_after:
            self._cleanup_after_test(test_case.name)

    def _cleanup_optimized_command_session(
        self,
        session_state: dict[str, object],
        current_test_name: str | None,
    ) -> None:
        if not session_state.get("needs_cleanup"):
            return
        cleanup_target = current_test_name
        if cleanup_target is None:
            if session_state.get("active_window") == APPLIST_TEST_CASE_NAME:
                cleanup_target = APPLIST_TEST_CASE_NAME
            elif session_state.get("cmd_open"):
                cleanup_target = EDGE_BROWSER_TEST_NAME
        cleanup_target = cleanup_target or EDGE_BROWSER_TEST_NAME
        self._cleanup_after_test(cleanup_target)
        session_state["cmd_open"] = False
        session_state["powershell_started"] = False
        session_state["needs_cleanup"] = False
        session_state["active_window"] = None

    def _result_to_log_entry(self, result: ExecutionResult) -> dict:
        return {
            "test_case": result.test_case_name,
            "status": result.status,
            "screenshots": [str(path) for path in result.evidence_paths],
            "log_path": str(result.log_path),
            "error": result.error_message,
            "requires_manual_check": result.requires_manual_check,
            "manual_check_message": result.manual_check_message,
            "manual_confirmation_required": result.manual_confirmation_required,
            "manual_confirmation_message": result.manual_confirmation_message,
            "manual_confirmation_screenshot": (
                str(result.manual_confirmation_screenshot)
                if result.manual_confirmation_screenshot
                else None
            ),
            "metadata": dict(result.metadata),
        }

    def _skipped_log_entry(self, test_name: str, result: ExecutionResult | None = None) -> dict:
        return {
            "test_case": test_name,
            "status": "Skipped",
            "screenshots": [str(path) for path in result.evidence_paths] if result else [],
            "log_path": str(result.log_path) if result else None,
            "error": None,
            "skip_reason": "Skipped by user",
            "metadata": dict(result.metadata) if result else {},
        }

    def _combined_edge_browser_log_entry(self, result: ExecutionResult) -> dict:
        screenshots = [
            str(path)
            for path in result.evidence_paths
            if path.name.casefold().startswith("edge_evidence_")
        ]
        if not screenshots:
            combined_path = result.metadata.get("combined_edge_browser_screenshot")
            if combined_path:
                screenshots = [str(combined_path)]
        metadata = dict(result.metadata)
        metadata["combined_with"] = EDGE_WEBVIEW_TEST_NAME
        return {
            "test_case": EDGE_BROWSER_TEST_NAME,
            "status": "Pass",
            "screenshots": screenshots,
            "log_path": str(result.log_path),
            "error": None,
            "combined_with": EDGE_WEBVIEW_TEST_NAME,
            "metadata": metadata,
        }

    def _should_combine_edge_registry(self, mandatory_order: list[str], index: int) -> bool:
        if mandatory_order[index] != EDGE_WEBVIEW_TEST_NAME:
            return False
        next_index = index + 1
        return next_index < len(mandatory_order) and mandatory_order[next_index] == EDGE_BROWSER_TEST_NAME

    def _record_mandatory_optimization_notes(self, mandatory_order: list[str]) -> None:
        command_block = [name for name in COMMAND_SESSION_TESTS if name in mandatory_order]
        if command_block:
            self.optimization_notes.append(
                {
                    "name": "command_session_mandatory_block",
                    "test_execution_order": command_block,
                    "scope": "suite",
                    "behavior": (
                        "Mandatory suite captures compatible command evidence in one shared CMD/PowerShell "
                        "session where possible; individual testcase runs are unchanged."
                    ),
                }
            )
            self._message(
                "Optimized mandatory command block planned: " + " -> ".join(command_block)
            )
        if EDGE_WEBVIEW_TEST_NAME in mandatory_order and EDGE_BROWSER_TEST_NAME in mandatory_order:
            self.optimization_notes.append(
                {
                    "name": "combined_edge_registry_session",
                    "test_execution_order": [EDGE_WEBVIEW_TEST_NAME, EDGE_BROWSER_TEST_NAME],
                    "scope": "suite",
                    "behavior": "Edge WebView and Edge browser registry evidence share one PowerShell session.",
                }
            )
        if "Google_and_Yahoo_Web_Access_Evidence" in mandatory_order:
            self.optimization_notes.append(
                {
                    "name": "single_browser_web_access_session",
                    "test_execution_order": ["Google_and_Yahoo_Web_Access_Evidence"],
                    "scope": "testcase",
                    "behavior": "Google and Yahoo evidence are captured from one Edge browser session.",
                }
            )

    def _runner_stop_event(self):
        if self.skip_event is None:
            return self.stop_event
        return CombinedStopSkipEvent(self.stop_event, self.skip_event)

    def _consume_skip_request(self) -> bool:
        if self._is_stop_requested():
            return False
        return consume_skip_request(self.skip_event)

    def _pause_for_manual_confirmation(self, result: ExecutionResult, scope: str) -> None:
        message = (
            result.manual_confirmation_message
            or "Hostname/IP evidence needs manual confirmation before continuing."
        )
        self._message(message, "WARNING")
        if self.pause_event is None or self.manual_confirmation_callback is None:
            return
        self.pause_event.set()
        self.manual_confirmation_callback(result)
        self._message(f"{scope} paused for Hostname/IP evidence confirmation.", "WARNING")
        wait_if_paused(self.pause_event, self.stop_event)
        self._message(f"{scope} resumed after Hostname/IP evidence confirmation.")

    def _cleanup_after_test(self, test_name: str) -> None:
        cleanup_started = time.perf_counter()
        cleanup_level = self._cleanup_level_for_test(test_name)
        self._message(f"Cleanup started after {test_name} ({cleanup_level})")
        context = AutomationContext(
            config=self.config,
            log_step=lambda message, level="INFO": self._message(f"Cleanup: {message}", level),
            citrix_desktop_name=self.citrix_desktop_name,
            stop_event=self.stop_event,
            pause_event=self.pause_event,
        )

        if test_name == "Hostname_and_IP_Evidence":
            context.press("esc")
            context.hotkey("alt", "f4")
            context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))
        elif test_name in {
            "Edge_WebView_Version_Evidence",
            "Edge_Browser_Version_Evidence",
        }:
            context.hotkey("alt", "f4")
            context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))
        elif test_name == "Zscaler_Services_Evidence":
            context.press("esc")
            context.hotkey("alt", "f4")
            context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))
        elif test_name == "Google_and_Yahoo_Web_Access_Evidence":
            context.hotkey("alt", "f4")
            context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))
        elif test_name == "Office_Applications_Launch":
            self._message("Cleanup: Office applications are closed inside the Office test flow")
            context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))
        elif test_name == "Applist_Validation_Evidence":
            context.press("esc")
            context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))
            context.press("esc")
            context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))
            self._message("Cleanup: Close Applist Notepad window")
            context.hotkey("alt", "f4")
            context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))
            self._message("Cleanup: Close Applist File Explorer window")
            context.hotkey("alt", "f4")
            context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))

        cleanup_seconds = round(time.perf_counter() - cleanup_started, 3)
        self.cleanup_timings.append(
            {
                "test_case": test_name,
                "cleanup_level": cleanup_level,
                "duration_seconds": cleanup_seconds,
            }
        )
        self._message(f"Cleanup completed after {test_name} ({cleanup_seconds} second(s), {cleanup_level})")

    def _cleanup_level_for_test(self, test_name: str) -> str:
        if test_name in {"Hostname_and_IP_Evidence", "Edge_WebView_Version_Evidence", EDGE_BROWSER_TEST_NAME}:
            return "medium"
        if test_name in {"Google_and_Yahoo_Web_Access_Evidence", "Zscaler_Services_Evidence", APPLIST_TEST_CASE_NAME}:
            return "medium"
        if test_name == "Office_Applications_Launch":
            return "light"
        return "light"

    def _write_master_log(
        self,
        started_at: datetime,
        ended_at: datetime,
        final_status: str,
        results: list[dict],
    ) -> Path:
        logs_dir = desktop_scoped_path(self.config.path("logs_dir"), self.citrix_desktop_name)
        logs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        path = logs_dir / f"Run_All_Evidence_{timestamp}.json"
        payload = {
            "feature_name": "Mandatory_Testcases",
            "citrix_desktop_name": self.citrix_desktop_name,
            "test_execution_order": mandatory_order_for_desktop(self.citrix_desktop_name),
            "start_time": started_at.replace(microsecond=0).isoformat(),
            "end_time": ended_at.replace(microsecond=0).isoformat(),
            "total_execution_time_seconds": round((ended_at - started_at).total_seconds(), 3),
            "passed_count": sum(1 for item in results if is_success_status(item["status"])),
            "failed_count": sum(1 for item in results if not is_success_status(item["status"])),
            "total_count": len(results),
            "between_tests_delay_seconds": self.config.wait("mandatory_between_tests_wait_sec", 2.0),
            "delay_confirmation": "Configured delay enforced between mandatory test cases.",
            "timings_seconds": {
                "cleanup": self.cleanup_timings,
            },
            "optimization_notes": self.optimization_notes,
            "final_status": final_status,
            "individual_results": results,
            "master_steps": self.master_steps,
        }
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)
        return path

    def _message(self, message: str, level: str = "INFO") -> None:
        self.master_steps.append(
            {
                "timestamp": datetime.now().replace(microsecond=0).isoformat(),
                "level": level,
                "message": message,
            }
        )
        self.status_callback(message)

    def _check_stop(self) -> None:
        if self._is_stop_requested():
            raise StopRequested()
        wait_if_paused(self.pause_event, self.stop_event)
        if self._is_stop_requested():
            raise StopRequested()

    def _is_stop_requested(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()


def _sleep(seconds: float, stop_event: Event | None = None, pause_event: Event | None = None) -> None:
    interruptible_sleep(seconds, stop_event, pause_event)


class ShakedownRunner:
    def __init__(
        self,
        config: AppConfig,
        citrix_desktop_name: str,
        status_callback: Callable[[str], None] | None = None,
        test_status_callback: Callable[[str, str], None] | None = None,
        stop_event: Event | None = None,
        pause_event: Event | None = None,
        skip_event: Event | None = None,
    ) -> None:
        self.config = config
        self.citrix_desktop_name = citrix_desktop_name
        self.status_callback = status_callback or (lambda message: None)
        self.test_status_callback = test_status_callback or (lambda test_id, status: None)
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.skip_event = skip_event
        self.master_steps: list[dict[str, str]] = []
        self.cleanup_timings: list[dict[str, object]] = []
        self.optimization_notes: list[dict[str, object]] = []

    def run(self, test_cases: list[TestCase]) -> MasterExecutionResult:
        started_at = datetime.now()
        tests_by_name = {test_case.name: test_case for test_case in test_cases}
        results = []
        stopped = False
        shakedown_edge_completed_tests: set[str] = set()

        self._message("Starting Shakedown Testcases")
        self._message(f"Citrix Desktop Name: {self.citrix_desktop_name}")
        self._record_shakedown_optimization_notes(tests_by_name)

        try:
            for index, test_name in enumerate(SHAKEDOWN_TEST_CASE_ORDER):
                self._check_stop()
                if test_name in shakedown_edge_completed_tests:
                    self._message(f"Shakedown sequence already completed by optimized Edge block: {test_name}")
                    continue

                test_case = tests_by_name.get(test_name)
                if test_case is None:
                    self._message(f"Missing shakedown test case: {test_name}", "ERROR")
                    results.append(
                        {
                            "test_case": test_name,
                            "status": "Fail",
                            "error": "Test case was not found in the GUI test list.",
                            "screenshots": [],
                            "log_path": None,
                        }
                    )
                    continue

                if self._should_run_shakedown_edge_session_block(index, tests_by_name):
                    block_result = self._run_shakedown_edge_session_block(tests_by_name)
                    results.extend(block_result.entries)
                    shakedown_edge_completed_tests.update(block_result.completed_test_names)
                    if block_result.stopped:
                        stopped = True
                        self._message("Shakedown Testcases stopped by user", "ERROR")
                        break
                    if index < len(SHAKEDOWN_TEST_CASE_ORDER) - 1:
                        delay = self.config.wait("shakedown_between_tests_wait_sec", 2.0)
                        self._message(f"Shakedown delay confirmed before next test: {delay} second(s)")
                        _sleep(delay, self.stop_event, self.pause_event)
                    continue

                if self._consume_skip_request():
                    self._message(f"Skip requested before {test_case.name}; marking testcase as Skipped.")
                    self.test_status_callback(test_case.id, "Skipped")
                    results.append(self._skipped_log_entry(test_case.name))
                    continue

                self.test_status_callback(test_case.id, "Running")
                self._message(f"Shakedown sequence running: {test_case.name}")
                result = TestRunner(
                    config=self.config,
                    citrix_desktop_name=self.citrix_desktop_name,
                    status_callback=self.status_callback,
                    stop_event=self._runner_stop_event(),
                    pause_event=self.pause_event,
                ).run(test_case)

                if result.status == "Stopped" and self._consume_skip_request():
                    self._message(f"Skip requested for {test_case.name}; marking testcase as Skipped and continuing.")
                    self.test_status_callback(test_case.id, "Skipped")
                    results.append(self._skipped_log_entry(test_case.name, result))
                    self._cleanup_after_test(test_case.name)
                    if index < len(SHAKEDOWN_TEST_CASE_ORDER) - 1:
                        delay = self.config.wait("shakedown_between_tests_wait_sec", 2.0)
                        self._message(f"Shakedown delay confirmed before next test: {delay} second(s)")
                        _sleep(delay, self.stop_event, self.pause_event)
                    continue

                self.test_status_callback(test_case.id, result.status)

                results.append(self._result_to_log_entry(result))
                if result.status == "Stopped":
                    stopped = True
                    self._message("Shakedown Testcases stopped by user", "ERROR")
                    break

                self._check_stop()
                self._cleanup_after_test(test_case.name)

                if index < len(SHAKEDOWN_TEST_CASE_ORDER) - 1:
                    delay = self.config.wait("shakedown_between_tests_wait_sec", 2.0)
                    self._message(f"Shakedown delay confirmed before next test: {delay} second(s)")
                    _sleep(delay, self.stop_event, self.pause_event)
        except StopRequested:
            stopped = True
            self._message("Shakedown Testcases stopped by user", "ERROR")

        ended_at = datetime.now()
        failed_count = sum(1 for item in results if not is_success_status(item["status"]))
        passed_count = sum(1 for item in results if is_success_status(item["status"]))
        total_count = len(results)
        duration_seconds = round((ended_at - started_at).total_seconds(), 3)
        final_status = "Stopped" if stopped or self._is_stop_requested() else ("Pass" if failed_count == 0 else "Fail")
        log_path = self._write_master_log(started_at, ended_at, final_status, results)
        self._message(f"Shakedown Testcases completed: {final_status}")
        self._message(f"Master log: {log_path}")
        return MasterExecutionResult(
            status=final_status,
            log_path=log_path,
            failed_count=failed_count,
            passed_count=passed_count,
            total_count=total_count,
            duration_seconds=duration_seconds,
        )

    def _should_run_shakedown_edge_session_block(
        self,
        index: int,
        tests_by_name: dict[str, TestCase],
    ) -> bool:
        if SHAKEDOWN_TEST_CASE_ORDER[index] != SHAKEDOWN_EDGE_SYNC_TEST_NAME:
            return False
        settings = self.config.raw.get("optimization", {})
        if isinstance(settings, dict) and settings.get("suite_shakedown_edge_session_enabled") is False:
            return False
        return len(self._shakedown_edge_session_block_names(tests_by_name)) >= 2

    def _shakedown_edge_session_block_names(self, tests_by_name: dict[str, TestCase]) -> list[str]:
        return [
            name
            for name in SHAKEDOWN_EDGE_SESSION_TESTS
            if name in SHAKEDOWN_TEST_CASE_ORDER and name in tests_by_name
        ]

    def _run_shakedown_edge_session_block(
        self,
        tests_by_name: dict[str, TestCase],
    ) -> ShakedownEdgeBlockResult:
        block_names = self._shakedown_edge_session_block_names(tests_by_name)
        block_result = ShakedownEdgeBlockResult(entries=[], completed_test_names=set())
        if not block_names:
            return block_result

        self._message("Optimized shakedown Edge block starting: " + " -> ".join(block_names))
        screenshots_dir = (
            desktop_scoped_path(self.config.path("screenshots_dir"), self.citrix_desktop_name)
            / SHAKEDOWN_EVIDENCE_FOLDER
        )
        logs_dir = desktop_scoped_path(self.config.path("logs_dir"), self.citrix_desktop_name)
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        context = AutomationContext(
            config=self.config,
            log_step=lambda message, level="INFO": self._message(message, level),
            citrix_desktop_name=self.citrix_desktop_name,
            evidence_category=SHAKEDOWN_EVIDENCE_FOLDER,
            stop_event=self.stop_event,
            pause_event=self.pause_event,
        )
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
        session_state: dict[str, object] = {
            "edge_open": False,
            "needs_cleanup": False,
            "fallback_only": False,
        }

        for test_name in block_names:
            self._check_stop()
            test_case = tests_by_name[test_name]
            if self._consume_skip_request():
                self._message(f"Skip requested before {test_case.name}; marking testcase as Skipped.")
                self.test_status_callback(test_case.id, "Skipped")
                block_result.entries.append(self._skipped_log_entry(test_case.name))
                block_result.completed_test_names.add(test_case.name)
                continue

            if session_state.get("fallback_only"):
                result = self._run_shakedown_edge_block_standalone(test_case)
                self._record_shakedown_edge_block_result(block_result, test_case, result, cleanup_after=True)
                if block_result.stopped:
                    break
                continue

            try:
                result = self._run_optimized_shakedown_edge_session_test(
                    test_case=test_case,
                    context=context,
                    screenshots=screenshots,
                    screenshots_dir=screenshots_dir,
                    logs_dir=logs_dir,
                    block_names=block_names,
                    session_state=session_state,
                )
                self._record_shakedown_edge_block_result(block_result, test_case, result, cleanup_after=False)
                if block_result.stopped:
                    break
            except Exception as exc:
                self._message(
                    f"Optimized shakedown Edge block failed during {test_case.name}: {exc}. "
                    "Falling back to the standalone testcase flow.",
                    "WARNING",
                )
                self._cleanup_optimized_edge_session(session_state)
                session_state["fallback_only"] = True
                result = self._run_shakedown_edge_block_standalone(test_case)
                self._record_shakedown_edge_block_result(block_result, test_case, result, cleanup_after=True)
                if block_result.stopped:
                    break

        if not block_result.stopped:
            self._cleanup_optimized_edge_session(session_state)
        self._message("Optimized shakedown Edge block finished")
        return block_result

    def _run_optimized_shakedown_edge_session_test(
        self,
        *,
        test_case: TestCase,
        context: AutomationContext,
        screenshots: ScreenshotManager,
        screenshots_dir: Path,
        logs_dir: Path,
        block_names: list[str],
        session_state: dict[str, object],
    ) -> ExecutionResult:
        self.test_status_callback(test_case.id, "Running")
        self._message(f"Shakedown Edge block running: {test_case.name}")
        execution_log = ExecutionLog(test_case.name, logs_dir, desktop_name=self.citrix_desktop_name)
        evidence_paths: list[Path] = []
        metadata: dict[str, object] = {
            "suite_optimized_edge_session": True,
            "optimized_block": "shakedown_edge_session_block",
            "optimized_block_order": list(block_names),
            "individual_testcase_run_unchanged": True,
        }
        screenshot_path: Path | None = None

        def add_step(message: str, level: str = "INFO") -> None:
            execution_log.add_step(message, level)
            self.status_callback(message)

        context.log_step = add_step
        context.evidence_paths = evidence_paths
        context.metadata = metadata

        try:
            replacement_started = time.perf_counter()
            deleted_count = remove_existing_evidence_for_test_case(screenshots_dir, test_case, add_step)
            metadata.setdefault("timings_seconds", {})["evidence_replacement_seconds"] = round(
                time.perf_counter() - replacement_started,
                3,
            )
            if deleted_count:
                add_step(f"Previous evidence replaced for this testcase: {deleted_count} screenshot(s) removed")

            add_step(f"Starting optimized shakedown Edge testcase: {test_case.name}")
            if test_case.name == SHAKEDOWN_EDGE_SYNC_TEST_NAME:
                screenshot_path = self._optimized_shakedown_edge_sync_evidence(
                    context,
                    screenshots,
                    evidence_paths,
                    add_step,
                    session_state,
                )
            elif test_case.name == SHAKEDOWN_EDGE_POLICY_PAC_TEST_NAME:
                screenshot_path = self._optimized_shakedown_edge_policy_pac_evidence(
                    context,
                    screenshots,
                    evidence_paths,
                    add_step,
                    session_state,
                )
            else:
                raise RuntimeError(f"Unsupported optimized shakedown Edge testcase: {test_case.name}")

            add_step("Optimized shakedown Edge testcase completed successfully")
            log_path = execution_log.finish("Pass", screenshot_path, evidence_paths, metadata)
            return ExecutionResult(
                "Pass",
                test_case.name,
                log_path,
                screenshot_path,
                tuple(evidence_paths),
                metadata=dict(metadata),
            )
        except StopRequested:
            add_step("Execution stopped by user", "ERROR")
            log_path = execution_log.finish("Stopped", screenshot_path, evidence_paths, metadata)
            return ExecutionResult(
                "Stopped",
                test_case.name,
                log_path,
                screenshot_path,
                tuple(evidence_paths),
                metadata=dict(metadata),
            )
        except Exception as exc:
            execution_log.set_error(exc)
            execution_log.finish("Fail", screenshot_path, evidence_paths, metadata)
            raise

    def _optimized_shakedown_edge_sync_evidence(
        self,
        context: AutomationContext,
        screenshots: ScreenshotManager,
        evidence_paths: list[Path],
        add_step: Callable[..., None],
        session_state: dict[str, object],
    ) -> Path:
        self._ensure_optimized_edge_window(context, add_step, session_state)

        context.step("Optimized Step: Open Edge Settings sync view with Alt + E, then G")
        context.hotkey("alt", "e")
        context.press("g")
        context.wait(self.config.wait("edge_settings_page_wait_sec", 10.0))

        context.step("Optimized Step: Check whether Edge profile Sign in button is visible")
        sign_in_target = find_sign_in_button_target(context)
        if sign_in_target is not None:
            sign_in_x, sign_in_y = sign_in_target
            context.step(
                "Edge Sign in button detected. Click Sign in "
                f"at detected coordinates ({sign_in_x}, {sign_in_y})"
            )
            context.click(
                sign_in_x,
                sign_in_y,
                wait_after_sec=self.config.wait("edge_profile_signin_wait_sec", 15.0),
            )
        else:
            context.step("Edge profile already appears signed in. Skipping Sign in click.")

        context.step("Optimized Step: Capture Edge sync evidence screenshot")
        sync_path = self._capture_optimized_shakedown_pass_screenshot(
            screenshots,
            context,
            evidence_paths,
            "edge_sync",
            add_step,
        )
        self._copy_optimized_shakedown_pass_screenshot_if_enabled(screenshots, sync_path, add_step)

        context.step("Optimized Step: Open Edge About/version page with Alt + E, then B, then M")
        context.hotkey("alt", "e")
        context.press("b")
        context.wait(1.0)
        context.press("m")
        context.wait(self.config.wait("edge_about_page_wait_sec", 5.0))

        context.step("Optimized Step: Capture Edge browser version evidence screenshot")
        version_path = self._capture_optimized_shakedown_pass_screenshot(
            screenshots,
            context,
            evidence_paths,
            "edge_browser_version",
            add_step,
        )
        self._copy_optimized_shakedown_pass_screenshot_if_enabled(screenshots, version_path, add_step)

        _validate_shakedown_edge_sync_evidence_screenshots(evidence_paths, add_step, self.config.raw)
        return version_path

    def _optimized_shakedown_edge_policy_pac_evidence(
        self,
        context: AutomationContext,
        screenshots: ScreenshotManager,
        evidence_paths: list[Path],
        add_step: Callable[..., None],
        session_state: dict[str, object],
    ) -> Path:
        self._ensure_optimized_edge_window(context, add_step, session_state)

        context.step(f"Optimized Step: Navigate to Edge policy page: {SHAKEDOWN_EDGE_POLICY_URL}")
        context.hotkey("alt", "d")
        context.wait(0.5)
        context.type_text(SHAKEDOWN_EDGE_POLICY_URL, interval=0.15)
        context.press("enter")
        context.wait(self.config.wait("edge_policy_page_wait_sec", 5.0))

        scroll_wait = self.config.wait("edge_policy_scroll_wait_sec", 2.0)

        context.step("Optimized Step: Press Page Down once and capture policy evidence part 1")
        context.press("pagedown", presses=1)
        context.wait(scroll_wait)
        policy_part_1 = self._capture_optimized_shakedown_pass_screenshot(
            screenshots,
            context,
            evidence_paths,
            "policy_pac_1",
            add_step,
        )
        self._copy_optimized_shakedown_pass_screenshot_if_enabled(screenshots, policy_part_1, add_step)

        context.step("Optimized Step: Press Page Down twice and capture policy evidence part 2")
        context.press("pagedown", presses=2)
        context.wait(scroll_wait)
        policy_part_2 = self._capture_optimized_shakedown_pass_screenshot(
            screenshots,
            context,
            evidence_paths,
            "policy_pac_2",
            add_step,
        )
        self._copy_optimized_shakedown_pass_screenshot_if_enabled(screenshots, policy_part_2, add_step)

        _validate_policy_pac_evidence_screenshots(context, add_step, self.config.raw)
        return policy_part_2

    def _ensure_optimized_edge_window(
        self,
        context: AutomationContext,
        add_step: Callable[..., None],
        session_state: dict[str, object],
    ) -> None:
        if session_state.get("edge_open"):
            return

        desktop_name = (self.citrix_desktop_name or "").strip()
        if not desktop_name:
            raise RuntimeError("Please enter the Citrix Desktop Name before starting the test.")

        context.step(f"Optimized Step: Activate Citrix desktop using user input: {desktop_name}")
        context.activate_window_by_title(
            desktop_name,
            exact=False,
            wait_after_sec=self.config.wait("citrix_activation_wait_sec", 4.0),
        )
        context.step("Optimized Step: Ensure Citrix input focus with a center-screen click")
        context.click_screen_center(wait_after_sec=self.config.wait("citrix_focus_click_wait_sec", 1.0))
        context.step("Optimized Step: Open Microsoft Edge from Windows Search")
        context.hotkey("winleft", "s")
        context.wait(self.config.wait("windows_search_wait_sec", 2.0))
        context.type_text(SHAKEDOWN_EDGE_SEARCH_TEXT, interval=0.15)
        context.wait(self.config.wait("edge_search_results_wait_sec", 10.0))
        context.press("enter")
        context.wait(self.config.wait("edge_launch_wait_sec", 10.0))
        context.step("Optimized Step: Maximize Microsoft Edge window")
        context.maximize_active_window()
        add_step("Optimized Edge browser session is ready")
        session_state["edge_open"] = True
        session_state["needs_cleanup"] = True

    def _capture_optimized_shakedown_pass_screenshot(
        self,
        screenshots: ScreenshotManager,
        context: AutomationContext,
        evidence_paths: list[Path],
        evidence_name: str,
        add_step: Callable[..., None],
    ) -> Path:
        screenshots.capture_region = context.capture_region()
        screenshot_path = screenshots.capture(evidence_name, "Pass")
        evidence_paths.append(screenshot_path)
        context.evidence_paths = evidence_paths
        add_step(f"Pass screenshot saved: {screenshot_path}")
        return screenshot_path

    def _copy_optimized_shakedown_pass_screenshot_if_enabled(
        self,
        screenshots: ScreenshotManager,
        screenshot_path: Path,
        add_step: Callable[..., None],
    ) -> None:
        if not self.config.screenshot_settings.get("copy_pass_screenshot_to_clipboard", True):
            return
        screenshots.copy_to_clipboard(screenshot_path)
        add_step("Pass screenshot copied to clipboard")

    def _run_shakedown_edge_block_standalone(self, test_case: TestCase) -> ExecutionResult:
        self.test_status_callback(test_case.id, "Running")
        self._message(f"Shakedown Edge block fallback running standalone: {test_case.name}")
        return TestRunner(
            config=self.config,
            citrix_desktop_name=self.citrix_desktop_name,
            status_callback=self.status_callback,
            stop_event=self._runner_stop_event(),
            pause_event=self.pause_event,
        ).run(test_case)

    def _record_shakedown_edge_block_result(
        self,
        block_result: ShakedownEdgeBlockResult,
        test_case: TestCase,
        result: ExecutionResult,
        *,
        cleanup_after: bool,
    ) -> None:
        if result.status == "Stopped" and self._consume_skip_request():
            self._message(f"Skip requested for {test_case.name}; marking testcase as Skipped and continuing.")
            self.test_status_callback(test_case.id, "Skipped")
            block_result.entries.append(self._skipped_log_entry(test_case.name, result))
            block_result.completed_test_names.add(test_case.name)
            if cleanup_after:
                self._cleanup_after_test(test_case.name)
            return

        self.test_status_callback(test_case.id, result.status)
        block_result.entries.append(self._result_to_log_entry(result))
        block_result.completed_test_names.add(test_case.name)

        if result.status == "Stopped":
            block_result.stopped = True
            return

        if cleanup_after:
            self._cleanup_after_test(test_case.name)

    def _cleanup_optimized_edge_session(self, session_state: dict[str, object]) -> None:
        if not session_state.get("needs_cleanup"):
            return
        cleanup_started = time.perf_counter()
        self._message("Shakedown optimized Edge cleanup started")
        context = AutomationContext(
            config=self.config,
            log_step=lambda message, level="INFO": self._message(f"Cleanup: {message}", level),
            citrix_desktop_name=self.citrix_desktop_name,
            stop_event=self.stop_event,
            pause_event=self.pause_event,
        )
        context.hotkey("alt", "f4")
        context.wait(self.config.wait("edge_close_wait_sec", 2.0))
        cleanup_seconds = round(time.perf_counter() - cleanup_started, 3)
        self.cleanup_timings.append(
            {
                "test_case": "shakedown_edge_session_block",
                "duration_seconds": cleanup_seconds,
            }
        )
        self._message(f"Shakedown optimized Edge cleanup completed ({cleanup_seconds} second(s))")
        session_state["edge_open"] = False
        session_state["needs_cleanup"] = False

    def _record_shakedown_optimization_notes(self, tests_by_name: dict[str, TestCase]) -> None:
        edge_block = self._shakedown_edge_session_block_names(tests_by_name)
        if len(edge_block) < 2:
            return
        settings = self.config.raw.get("optimization", {})
        if isinstance(settings, dict) and settings.get("suite_shakedown_edge_session_enabled") is False:
            return
        self.optimization_notes.append(
            {
                "name": "shakedown_edge_session_block",
                "test_execution_order": edge_block,
                "scope": "suite",
                "behavior": (
                    "Shakedown suite captures Edge Sync and Edge Policy PAC evidence in one shared "
                    "Edge browser session where possible; individual testcase runs are unchanged."
                ),
            }
        )
        self._message("Optimized shakedown Edge block planned: " + " -> ".join(edge_block))

    def _result_to_log_entry(self, result: ExecutionResult) -> dict:
        return {
            "test_case": result.test_case_name,
            "status": result.status,
            "screenshots": [str(path) for path in result.evidence_paths],
            "log_path": str(result.log_path),
            "error": result.error_message,
            "metadata": dict(result.metadata),
        }

    def _skipped_log_entry(self, test_name: str, result: ExecutionResult | None = None) -> dict:
        return {
            "test_case": test_name,
            "status": "Skipped",
            "screenshots": [str(path) for path in result.evidence_paths] if result else [],
            "log_path": str(result.log_path) if result else None,
            "error": None,
            "skip_reason": "Skipped by user",
            "metadata": dict(result.metadata) if result else {},
        }

    def _runner_stop_event(self):
        if self.skip_event is None:
            return self.stop_event
        return CombinedStopSkipEvent(self.stop_event, self.skip_event)

    def _consume_skip_request(self) -> bool:
        if self._is_stop_requested():
            return False
        return consume_skip_request(self.skip_event)

    def _cleanup_after_test(self, test_name: str) -> None:
        cleanup_started = time.perf_counter()
        self._message(f"Shakedown cleanup confirmation started after {test_name}")
        context = AutomationContext(
            config=self.config,
            log_step=lambda message, level="INFO": self._message(f"Cleanup: {message}", level),
            citrix_desktop_name=self.citrix_desktop_name,
            stop_event=self.stop_event,
            pause_event=self.pause_event,
        )
        context.press("esc")
        context.wait(self.config.wait("shakedown_cleanup_confirm_wait_sec", 2.0))
        cleanup_seconds = round(time.perf_counter() - cleanup_started, 3)
        self.cleanup_timings.append(
            {
                "test_case": test_name,
                "duration_seconds": cleanup_seconds,
            }
        )
        self._message(f"Shakedown cleanup confirmation completed after {test_name} ({cleanup_seconds} second(s))")

    def _write_master_log(
        self,
        started_at: datetime,
        ended_at: datetime,
        final_status: str,
        results: list[dict],
    ) -> Path:
        logs_dir = desktop_scoped_path(self.config.path("logs_dir"), self.citrix_desktop_name)
        logs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        path = logs_dir / f"Run_All_Shakedown_Testcases_{timestamp}.json"
        payload = {
            "feature_name": "Shakedown_Testcases",
            "citrix_desktop_name": self.citrix_desktop_name,
            "test_execution_order": SHAKEDOWN_TEST_CASE_ORDER,
            "start_time": started_at.replace(microsecond=0).isoformat(),
            "end_time": ended_at.replace(microsecond=0).isoformat(),
            "total_execution_time_seconds": round((ended_at - started_at).total_seconds(), 3),
            "passed_count": sum(1 for item in results if is_success_status(item["status"])),
            "failed_count": sum(1 for item in results if not is_success_status(item["status"])),
            "total_count": len(results),
            "between_tests_delay_seconds": self.config.wait("shakedown_between_tests_wait_sec", 2.0),
            "delay_confirmation": "Configured delay enforced between shakedown test cases.",
            "timings_seconds": {
                "cleanup": self.cleanup_timings,
            },
            "optimization_notes": self.optimization_notes,
            "final_status": final_status,
            "individual_results": results,
            "master_steps": self.master_steps,
        }
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)
        return path

    def _message(self, message: str, level: str = "INFO") -> None:
        self.master_steps.append(
            {
                "timestamp": datetime.now().replace(microsecond=0).isoformat(),
                "level": level,
                "message": message,
            }
        )
        self.status_callback(message)

    def _check_stop(self) -> None:
        if self._is_stop_requested():
            raise StopRequested()
        wait_if_paused(self.pause_event, self.stop_event)
        if self._is_stop_requested():
            raise StopRequested()

    def _is_stop_requested(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()


class CompleteTestingRunner:
    def __init__(
        self,
        config: AppConfig,
        citrix_desktop_name: str,
        status_callback: Callable[[str], None] | None = None,
        test_status_callback: Callable[[str, str], None] | None = None,
        phase_status_callback: Callable[[str, str], None] | None = None,
        manual_confirmation_callback: Callable[[ExecutionResult], None] | None = None,
        stop_event: Event | None = None,
        pause_event: Event | None = None,
        skip_event: Event | None = None,
    ) -> None:
        self.config = config
        self.citrix_desktop_name = citrix_desktop_name
        self.status_callback = status_callback or (lambda message: None)
        self.test_status_callback = test_status_callback or (lambda test_id, status: None)
        self.phase_status_callback = phase_status_callback or (lambda phase, status: None)
        self.manual_confirmation_callback = manual_confirmation_callback
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.skip_event = skip_event
        self.master_steps: list[dict[str, str]] = []
        self.phase_timings: dict[str, float] = {}
        self.cleanup_timings: list[dict[str, object]] = []
        self.execution_plan: dict[str, object] = {}

    def run(self, test_cases: list[TestCase]) -> CompleteExecutionResult:
        started_at = datetime.now()
        tests_by_name = {test_case.name: test_case for test_case in test_cases}
        stopped = False
        mandatory_payload: dict = {}
        shakedown_payload: dict = {}
        iat_results: list[dict] = []
        silo43_results: list[dict] = []
        post_complete_results: list[dict] = []
        manual_check_required = False
        manual_check_message = None
        run_silo43_phase = is_silo43_desktop(self.citrix_desktop_name)

        mandatory_status = "Fail"
        shakedown_status = "Fail"
        silo43_status = "Fail" if run_silo43_phase else "Skipped"
        iat_status = "Fail"

        self._message("Starting Perform Complete Testing")
        self._message(f"Citrix Desktop Name: {self.citrix_desktop_name}")
        self.execution_plan = self._build_execution_plan()
        self._log_execution_plan(self.execution_plan)

        try:
            phase_started = time.perf_counter()
            self._run_preflight_once()
            self.phase_timings["preflight"] = round(time.perf_counter() - phase_started, 3)

            self.phase_status_callback("mandatory", "Running")
            phase_started = time.perf_counter()
            mandatory_result = MasterRunner(
                config=self.config,
                citrix_desktop_name=self.citrix_desktop_name,
                status_callback=self.status_callback,
                test_status_callback=self.test_status_callback,
                manual_confirmation_callback=self.manual_confirmation_callback,
                stop_event=self.stop_event,
                pause_event=self.pause_event,
                skip_event=self.skip_event,
            ).run(test_cases)
            self.phase_timings["mandatory"] = round(time.perf_counter() - phase_started, 3)
            mandatory_status = mandatory_result.status
            self.phase_status_callback("mandatory", mandatory_status)
            mandatory_payload = _read_json_log(mandatory_result.log_path)
            stopped = mandatory_status == "Stopped"
            manual_check_required = mandatory_result.manual_check_required
            manual_check_message = mandatory_result.manual_check_message

            if manual_check_required:
                shakedown_status = "Skipped"
                silo43_status = "Skipped"
                iat_status = "Skipped"
                self.phase_status_callback("shakedown", shakedown_status)
                if run_silo43_phase:
                    self.phase_status_callback("silo43", silo43_status)
                self.phase_status_callback("iat", iat_status)
                self._message(
                    manual_check_message
                    or "Manual check required before continuing Complete Testing.",
                    "ERROR",
                )
                self._message(
                    "Complete Testing stopped before Shakedown because Hostname_and_IP_Evidence needs manual review.",
                    "ERROR",
                )

            if not stopped and not manual_check_required:
                self._check_stop()
                phase_delay = self.config.wait("complete_phase_transition_wait_sec", 2.0)
                self._message(f"Complete Testing delay before Shakedown: {phase_delay} second(s)")
                _sleep(phase_delay, self.stop_event, self.pause_event)

                self.phase_status_callback("shakedown", "Running")
                phase_started = time.perf_counter()
                shakedown_result = ShakedownRunner(
                    config=self.config,
                    citrix_desktop_name=self.citrix_desktop_name,
                    status_callback=self.status_callback,
                    test_status_callback=self.test_status_callback,
                    stop_event=self.stop_event,
                    pause_event=self.pause_event,
                    skip_event=self.skip_event,
                ).run(test_cases)
                self.phase_timings["shakedown"] = round(time.perf_counter() - phase_started, 3)
                shakedown_status = shakedown_result.status
                self.phase_status_callback("shakedown", shakedown_status)
                shakedown_payload = _read_json_log(shakedown_result.log_path)
                stopped = shakedown_status == "Stopped"

            if run_silo43_phase and not stopped and not manual_check_required:
                self._check_stop()
                phase_delay = self.config.wait("complete_phase_transition_wait_sec", 2.0)
                self._message(f"Complete Testing delay before Silo 43 Testcases: {phase_delay} second(s)")
                _sleep(phase_delay, self.stop_event, self.pause_event)

                self.phase_status_callback("silo43", "Running")
                phase_started = time.perf_counter()
                silo43_results = self._run_silo43_tests(tests_by_name)
                self.phase_timings["silo43"] = round(time.perf_counter() - phase_started, 3)
                silo43_status = (
                    "Pass"
                    if silo43_results and all(is_success_status(item["status"]) for item in silo43_results)
                    else "Fail"
                )
                self.phase_status_callback("silo43", silo43_status)

            if not stopped and not manual_check_required:
                self._check_stop()
                phase_delay = self.config.wait("complete_phase_transition_wait_sec", 2.0)
                self._message(f"Complete Testing delay before IAT: {phase_delay} second(s)")
                _sleep(phase_delay, self.stop_event, self.pause_event)

                self.phase_status_callback("iat", "Running")
                phase_started = time.perf_counter()
                iat_results = self._run_iat_tests(tests_by_name)
                self.phase_timings["iat"] = round(time.perf_counter() - phase_started, 3)
                iat_status = "Pass" if iat_results and all(is_success_status(item["status"]) for item in iat_results) else "Fail"
                self.phase_status_callback("iat", iat_status)

            if not stopped and not manual_check_required:
                self._check_stop()
                self.phase_status_callback("post_complete", "Running")
                phase_started = time.perf_counter()
                post_complete_result = self._capture_post_complete_zscaler_evidence()
                self.phase_timings["post_complete_zscaler"] = round(time.perf_counter() - phase_started, 3)
                post_complete_results.append(post_complete_result)
                self.phase_status_callback("post_complete", post_complete_result["status"])
                _append_payload_results(mandatory_payload, post_complete_results)
        except StopRequested:
            stopped = True
            self._message("Perform Complete Testing stopped by user", "ERROR")

        ended_at = datetime.now()
        phase_statuses = [mandatory_status, shakedown_status, iat_status]
        if run_silo43_phase:
            phase_statuses.append(silo43_status)
        failed_count = sum(1 for status in phase_statuses if not is_success_status(status))
        failed_count += sum(1 for item in post_complete_results if not is_success_status(item.get("status", "Fail")))
        result_items = _combined_result_items(
            mandatory_payload,
            shakedown_payload,
            iat_results,
            post_complete_results,
            silo43_results,
        )
        passed_count = sum(1 for item in result_items if is_success_status(item.get("status", "Fail")))
        total_count = len(result_items)
        duration_seconds = round((ended_at - started_at).total_seconds(), 3)
        final_status = "Stopped" if stopped or self._is_stop_requested() else ("Pass" if failed_count == 0 else "Fail")
        log_path = self._write_complete_log(
            started_at,
            ended_at,
            final_status,
            mandatory_payload,
            shakedown_payload,
            iat_results,
            silo43_results,
            mandatory_status,
            shakedown_status,
            iat_status,
            silo43_status,
            manual_check_required,
            manual_check_message,
            passed_count,
            total_count,
        )
        if manual_check_required:
            report_path = None
            self._message("Word report generation skipped until Hostname/IP evidence is manually checked.")
        else:
            report_path = self._generate_word_report(log_path, final_status)
        self._message(f"Perform Complete Testing completed: {final_status}")
        self._message(f"Complete Testing log: {log_path}")
        return CompleteExecutionResult(
            status=final_status,
            log_path=log_path,
            failed_count=failed_count,
            mandatory_status=mandatory_status,
            shakedown_status=shakedown_status,
            iat_status=iat_status,
            silo43_status=silo43_status,
            report_path=report_path,
            manual_check_required=manual_check_required,
            manual_check_message=manual_check_message,
            passed_count=passed_count,
            total_count=total_count,
            duration_seconds=duration_seconds,
        )

    def _skipped_log_entry(self, test_name: str, result: ExecutionResult | None = None) -> dict:
        return {
            "test_case": test_name,
            "status": "Skipped",
            "screenshots": [str(path) for path in result.evidence_paths] if result else [],
            "log_path": str(result.log_path) if result else None,
            "error": None,
            "skip_reason": "Skipped by user",
            "metadata": dict(result.metadata) if result else {},
        }

    def _runner_stop_event(self):
        if self.skip_event is None:
            return self.stop_event
        return CombinedStopSkipEvent(self.stop_event, self.skip_event)

    def _consume_skip_request(self) -> bool:
        if self._is_stop_requested():
            return False
        return consume_skip_request(self.skip_event)

    def _run_iat_tests(self, tests_by_name: dict[str, TestCase]) -> list[dict]:
        results = []
        for test_name in IAT_TEST_CASE_ORDER:
            self._check_stop()
            test_case = tests_by_name.get(test_name)
            if test_case is None:
                self._message(f"Missing IAT test case: {test_name}", "ERROR")
                results.append(
                    {
                        "test_case": test_name,
                        "status": "Fail",
                        "screenshots": [],
                        "log_path": None,
                        "error": "Test case was not found in the GUI test list.",
                    }
                )
                continue

            if self._consume_skip_request():
                self._message(f"Skip requested before {test_case.name}; marking testcase as Skipped.")
                self.test_status_callback(test_case.id, "Skipped")
                results.append(self._skipped_log_entry(test_case.name))
                continue

            self.test_status_callback(test_case.id, "Running")
            self._message(f"IAT sequence running: {test_case.name}")
            result = TestRunner(
                config=self.config,
                citrix_desktop_name=self.citrix_desktop_name,
                status_callback=self.status_callback,
                stop_event=self._runner_stop_event(),
                pause_event=self.pause_event,
            ).run(test_case)
            if result.status == "Stopped" and self._consume_skip_request():
                self._message(f"Skip requested for {test_case.name}; marking testcase as Skipped and continuing.")
                self.test_status_callback(test_case.id, "Skipped")
                results.append(self._skipped_log_entry(test_case.name, result))
                self._cleanup_after_iat(test_case.name)
                continue

            self.test_status_callback(test_case.id, result.status)
            results.append(
                {
                    "test_case": result.test_case_name,
                    "status": result.status,
                    "screenshots": [str(path) for path in result.evidence_paths],
                    "log_path": str(result.log_path),
                    "error": result.error_message,
                    "metadata": dict(result.metadata),
                }
            )
            self._cleanup_after_iat(test_case.name)
        return results

    def _build_execution_plan(self) -> dict[str, object]:
        mandatory_order = mandatory_order_for_desktop(self.citrix_desktop_name)
        desktop_type = self._desktop_type_label()
        phases = ["mandatory", "shakedown"]
        if desktop_type == "SILO43":
            phases.append("silo43")
        phases.extend(["iat", "post_complete_zscaler"])
        command_block = [name for name in COMMAND_SESSION_TESTS if name in mandatory_order]
        return {
            "desktop_type": desktop_type,
            "phases": phases,
            "mandatory_order": mandatory_order,
            "shakedown_order": list(SHAKEDOWN_TEST_CASE_ORDER),
            "iat_order": list(IAT_TEST_CASE_ORDER),
            "silo43_order": list(SILO43_TEST_CASE_ORDER) if desktop_type == "SILO43" else [],
            "optimized_groups": [
                {
                    "name": "mandatory_command_session_block",
                    "testcases": command_block,
                    "notes": (
                        "Suite-level command evidence uses one shared CMD/PowerShell session where possible, "
                        "while report ordering and individual testcase runs remain unchanged."
                    ),
                },
                {
                    "name": "browser_access_block",
                    "testcases": ["Google_and_Yahoo_Web_Access_Evidence"],
                    "notes": "Google and Yahoo evidence are captured in one Edge browser session.",
                },
                {
                    "name": "zscaler_reuse",
                    "testcases": ["Zscaler_Services_Evidence", POST_COMPLETE_ZSCALER_TEST_NAME],
                    "notes": "Post-complete capture checks for an existing healthy ZCCVDI window before relaunching.",
                },
            ],
            "cleanup_strategy": {
                "light": "Esc/confirmation only for already self-cleaning tests.",
                "medium": "Close the active app/window after evidence capture.",
                "heavy": "Reserved for future desktop reset when screen context is lost.",
            },
            "individual_testcase_runs_unchanged": True,
        }

    def _desktop_type_label(self) -> str:
        if is_silo43_desktop(self.citrix_desktop_name):
            return "SILO43"
        if is_ring0_desktop(self.citrix_desktop_name):
            return "RING0"
        return "TEST"

    def _log_execution_plan(self, plan: dict[str, object]) -> None:
        self._message(f"Complete Testing desktop type: {plan['desktop_type']}")
        self._message("Complete Testing phase plan: " + " -> ".join(plan["phases"]))
        self._message("Mandatory execution plan: " + " -> ".join(plan["mandatory_order"]))
        if plan["silo43_order"]:
            self._message("Silo 43 phase included for this desktop.")
        else:
            self._message("Silo 43 phase excluded for this desktop.")

    def _run_preflight_once(self) -> None:
        self._message("Complete Testing preflight started")
        screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), self.citrix_desktop_name)
        logs_dir = desktop_scoped_path(self.config.path("logs_dir"), self.citrix_desktop_name)
        for folder_name in (MANDATORY_EVIDENCE_FOLDER, SHAKEDOWN_EVIDENCE_FOLDER, IAT_EVIDENCE_FOLDER):
            (screenshots_root / folder_name).mkdir(parents=True, exist_ok=True)
        if is_silo43_desktop(self.citrix_desktop_name):
            (screenshots_root / SILO43_EVIDENCE_FOLDER).mkdir(parents=True, exist_ok=True)
        logs_dir.mkdir(parents=True, exist_ok=True)

        context = AutomationContext(
            config=self.config,
            log_step=lambda message, level="INFO": self._message(f"Preflight: {message}", level),
            citrix_desktop_name=self.citrix_desktop_name,
            stop_event=self._runner_stop_event(),
            pause_event=self.pause_event,
        )
        try:
            context.step(f"Activate Citrix desktop once for Complete Testing: {self.citrix_desktop_name}")
            context.activate_window_by_title(
                self.citrix_desktop_name,
                exact=False,
                wait_after_sec=self.config.wait("citrix_activation_wait_sec", 4.0),
            )
            context.step("Confirm Citrix input focus with a center-screen click")
            context.click_screen_center(wait_after_sec=self.config.wait("citrix_focus_click_wait_sec", 1.0))
        except Exception as exc:
            self._message(f"Complete Testing preflight warning: {exc}", "WARNING")
        self._message("Complete Testing preflight completed")

    def _run_silo43_tests(self, tests_by_name: dict[str, TestCase]) -> list[dict]:
        results = []
        for index, test_name in enumerate(SILO43_TEST_CASE_ORDER):
            self._check_stop()
            test_case = tests_by_name.get(test_name)
            if test_case is None:
                self._message(f"Missing Silo 43 test case: {test_name}", "ERROR")
                results.append(
                    {
                        "test_case": test_name,
                        "status": "Fail",
                        "screenshots": [],
                        "log_path": None,
                        "error": "Test case was not found in the GUI test list.",
                    }
                )
                continue

            if self._consume_skip_request():
                self._message(f"Skip requested before {test_case.name}; marking testcase as Skipped.")
                self.test_status_callback(test_case.id, "Skipped")
                results.append(self._skipped_log_entry(test_case.name))
                continue

            self.test_status_callback(test_case.id, "Running")
            self._message(f"Silo 43 sequence running: {test_case.name}")
            result = TestRunner(
                config=self.config,
                citrix_desktop_name=self.citrix_desktop_name,
                status_callback=self.status_callback,
                stop_event=self._runner_stop_event(),
                pause_event=self.pause_event,
            ).run(test_case)
            if result.status == "Stopped" and self._consume_skip_request():
                self._message(f"Skip requested for {test_case.name}; marking testcase as Skipped and continuing.")
                self.test_status_callback(test_case.id, "Skipped")
                results.append(self._skipped_log_entry(test_case.name, result))
                self._cleanup_after_silo43(test_case.name)
                continue

            self.test_status_callback(test_case.id, result.status)
            results.append(
                {
                    "test_case": result.test_case_name,
                    "status": result.status,
                    "screenshots": [str(path) for path in result.evidence_paths],
                    "log_path": str(result.log_path),
                    "error": result.error_message,
                    "metadata": dict(result.metadata),
                }
            )
            self._cleanup_after_silo43(test_case.name)

            if index < len(SILO43_TEST_CASE_ORDER) - 1:
                delay = self.config.wait("silo43_between_tests_wait_sec", 2.0)
                if delay > 0:
                    self._message(f"Silo 43 delay before next test: {delay} second(s)")
                    _sleep(delay, self.stop_event, self.pause_event)
        return results

    def _cleanup_after_silo43(self, test_name: str) -> None:
        MasterRunner(
            config=self.config,
            citrix_desktop_name=self.citrix_desktop_name,
            status_callback=lambda message: self._message(f"Silo 43 cleanup: {message}"),
            stop_event=self.stop_event,
            pause_event=self.pause_event,
        )._cleanup_after_test(test_name)

    def _cleanup_after_iat(self, test_name: str) -> None:
        cleanup_started = time.perf_counter()
        self._message(f"IAT cleanup confirmation started after {test_name}")
        context = AutomationContext(
            config=self.config,
            log_step=lambda message, level="INFO": self._message(f"Cleanup: {message}", level),
            citrix_desktop_name=self.citrix_desktop_name,
            stop_event=self.stop_event,
            pause_event=self.pause_event,
        )
        context.press("esc")
        context.wait(self.config.wait("complete_cleanup_confirm_wait_sec", 2.0))
        context.hotkey("alt", "f4")
        context.wait(self.config.wait("complete_cleanup_confirm_wait_sec", 2.0))
        cleanup_seconds = round(time.perf_counter() - cleanup_started, 3)
        self.cleanup_timings.append(
            {
                "test_case": test_name,
                "duration_seconds": cleanup_seconds,
            }
        )
        self._message(f"IAT cleanup confirmation completed after {test_name} ({cleanup_seconds} second(s))")

    def _capture_post_complete_zscaler_evidence(self) -> dict:
        if self._consume_skip_request():
            self._message("Skip requested before post-complete ZScaler evidence; marking phase as Skipped.")
            return {
                "test_case": POST_COMPLETE_ZSCALER_TEST_NAME,
                "status": "Skipped",
                "screenshots": [],
                "log_path": None,
                "error": None,
                "skip_reason": "Skipped by user",
                "capture_timing": "After final IAT testcase during Perform Complete Testing",
            }

        self._message("Post-complete ZScaler evidence capture started")
        screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), self.citrix_desktop_name)
        remove_existing_evidence_for_prefixes(
            screenshots_root / MANDATORY_EVIDENCE_FOLDER,
            ("zscaler_services_2",),
            lambda message: self._message(f"Post-complete ZScaler: {message}"),
        )
        context = AutomationContext(
            config=self.config,
            log_step=lambda message, level="INFO": self._message(f"Post-complete ZScaler: {message}", level),
            citrix_desktop_name=self.citrix_desktop_name,
            evidence_category=MANDATORY_EVIDENCE_FOLDER,
            stop_event=self._runner_stop_event(),
            pause_event=self.pause_event,
        )
        screenshot_path = None
        status = "Fail"
        error = None
        try:
            context.step(f"Activate Citrix desktop using user input: {self.citrix_desktop_name}")
            context.activate_window_by_title(
                self.citrix_desktop_name,
                exact=False,
                wait_after_sec=self.config.wait("citrix_activation_wait_sec", 4.0),
            )
            context.step("Ensure Citrix input focus with a center-screen click")
            context.click_screen_center(wait_after_sec=self.config.wait("citrix_focus_click_wait_sec", 1.0))
            for attempt in range(1, 3):
                if attempt > 1:
                    context.step("Relaunch ZCCVDI after failed screenshot validation before retry capture")
                    context.hotkey("alt", "f4")
                    context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))
                    self._launch_post_complete_zscaler(context)

                self._ensure_post_complete_zscaler_ready(context)
                self._recover_or_retry_post_complete_zscaler(context)
                screenshot_path = context.capture_evidence("zscaler_services_2", status="Pass")
                validation = validate_zscaler_services_with_windows_ocr(screenshot_path)
                if validation.valid:
                    status = "Pass"
                    error = None
                    self._message(f"Post-complete ZScaler OCR validation passed on attempt {attempt} of 2")
                    self._message(f"Post-complete ZScaler evidence captured: {screenshot_path}")
                    break

                error = f"Post-complete ZScaler screenshot validation failed: {validation.reason}"
                self._message(
                    f"Post-complete ZScaler OCR validation failed on attempt {attempt} of 2: {validation.reason}",
                    "ERROR",
                )
                remove_existing_evidence_for_prefixes(
                    screenshots_root / MANDATORY_EVIDENCE_FOLDER,
                    ("zscaler_services_2",),
                    lambda message: self._message(f"Post-complete ZScaler: {message}"),
                )
                if attempt == 1:
                    self._message("Validation failed after capture; relaunching ZCCVDI and retrying once.")
                    continue

                screenshot_path = context.capture_evidence("zscaler_services_2", status="Fail")
                self._message(f"Post-complete ZScaler failed evidence captured after retry: {screenshot_path}", "ERROR")
        except StopRequested:
            if self._consume_skip_request():
                status = "Skipped"
                error = None
                self._message("Skip requested for post-complete ZScaler evidence; marking phase as Skipped.")
            else:
                raise
        except Exception as exc:
            error = str(exc)
            self._message(f"Post-complete ZScaler evidence failed: {exc}", "ERROR")
        finally:
            try:
                context.hotkey("alt", "f4")
                context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))
            except Exception as exc:
                self._message(f"Post-complete ZScaler cleanup warning: {exc}", "ERROR")

        return {
            "test_case": POST_COMPLETE_ZSCALER_TEST_NAME,
            "status": status,
            "screenshots": [str(screenshot_path)] if screenshot_path else [],
            "log_path": None,
            "error": error,
            "capture_timing": "After final IAT testcase during Perform Complete Testing",
        }

    def rerun_post_complete_zscaler_evidence(self) -> dict:
        return self._capture_post_complete_zscaler_evidence()

    def _ensure_post_complete_zscaler_ready(self, context: AutomationContext) -> None:
        context.step("Check whether an existing ZCCVDI window is already visible and healthy")
        state = self._poll_post_complete_zscaler_state(
            context,
            timeout_sec=self.config.wait("zscaler_post_complete_reuse_poll_timeout_sec", 4.0),
        )
        if state == "healthy":
            context.step("Existing ZCCVDI window is healthy. Reusing it for post-complete evidence.")
            return
        if state == "problem":
            context.step("Existing ZCCVDI window is visible but unhealthy. Attempting recovery before relaunch.")
            recover_zscaler_connection_if_needed(context)
            if self._poll_post_complete_zscaler_state(
                context,
                timeout_sec=self.config.wait("zscaler_post_complete_ready_poll_timeout_sec", 10.0),
            ) == "healthy":
                context.step("Existing ZCCVDI window recovered successfully. Reusing it for post-complete evidence.")
                return

        context.step("No healthy existing ZCCVDI window found. Launching ZCCVDI for post-complete evidence.")
        self._launch_post_complete_zscaler(context)

    def _launch_post_complete_zscaler(self, context: AutomationContext) -> None:
        context.step("Open Windows Search using Windows + S")
        context.hotkey("winleft", "s")
        context.wait(self.config.wait("windows_search_wait_sec", 2.0))
        context.step("Search and launch application: Apps: ZCCVDI")
        context.type_text("Apps: ZCCVDI", interval=0.15)
        context.wait(self.config.wait("windows_search_results_wait_sec", 5.0))
        context.press("enter")
        context.step("Wait for Zscaler Client Connector VDI application to open")
        context.wait(self.config.wait("zscaler_launch_wait_sec", 20.0))

    def _recover_or_retry_post_complete_zscaler(self, context: AutomationContext) -> None:
        ready_timeout_sec = self.config.wait("zscaler_post_complete_ready_poll_timeout_sec", 10.0)
        for attempt in range(2):
            state = self._poll_post_complete_zscaler_state(context, timeout_sec=ready_timeout_sec)
            if state == "healthy":
                return
            if state == "problem":
                recover_zscaler_connection_if_needed(context)
                if self._poll_post_complete_zscaler_state(context, timeout_sec=ready_timeout_sec) == "healthy":
                    return

            if attempt == 0:
                context.step(
                    "Zscaler window/status did not become healthy before post-complete capture. "
                    "Relaunching ZCCVDI once."
                )
                context.hotkey("alt", "f4")
                context.wait(self.config.wait("cleanup_short_wait_sec", 2.0))
                self._launch_post_complete_zscaler(context)
                continue

            if state == "problem":
                raise RuntimeError("Zscaler still shows OFF / CONNECTION ERROR after retry.")
            raise RuntimeError("Zscaler Client Connector did not become visible and healthy before post-complete capture.")

    def _poll_post_complete_zscaler_state(self, context: AutomationContext, timeout_sec: float | None = None) -> str:
        timeout_sec = timeout_sec if timeout_sec is not None else self.config.wait("zscaler_status_poll_timeout_sec", 18.0)
        interval_sec = self.config.wait("zscaler_status_poll_interval_sec", 1.0)
        attempts = max(1, int(timeout_sec / max(interval_sec, 0.1)))
        for attempt in range(1, attempts + 1):
            context.step(f"Post-complete Zscaler status poll {attempt} of {attempts}")
            if zscaler_healthy_state_visible(context):
                return "healthy"
            if zscaler_problem_state_visible(context):
                return "problem"
            context.wait(interval_sec)
        return "unknown"

    def _write_complete_log(
        self,
        started_at: datetime,
        ended_at: datetime,
        final_status: str,
        mandatory_payload: dict,
        shakedown_payload: dict,
        iat_results: list[dict],
        silo43_results: list[dict],
        mandatory_status: str,
        shakedown_status: str,
        iat_status: str,
        silo43_status: str,
        manual_check_required: bool = False,
        manual_check_message: str | None = None,
        passed_count: int = 0,
        total_count: int = 0,
    ) -> Path:
        logs_dir = desktop_scoped_path(self.config.path("logs_dir"), self.citrix_desktop_name)
        logs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = started_at.strftime("%Y%m%d_%H%M%S")
        path = logs_dir / f"Perform_Complete_Testing_{timestamp}.json"
        screenshots_root = desktop_scoped_path(self.config.path("screenshots_dir"), self.citrix_desktop_name)
        payload = {
            "feature_name": "Perform Complete Testing",
            "citrix_desktop_name": self.citrix_desktop_name,
            "start_time": started_at.replace(microsecond=0).isoformat(),
            "end_time": ended_at.replace(microsecond=0).isoformat(),
            "total_execution_duration_seconds": round((ended_at - started_at).total_seconds(), 3),
            "passed_count": passed_count,
            "failed_count": max(total_count - passed_count, 0),
            "total_count": total_count,
            "phase_transition_delay_seconds": self.config.wait("complete_phase_transition_wait_sec", 2.0),
            "execution_plan": self.execution_plan,
            "timings_seconds": {
                "phases": dict(self.phase_timings),
                "iat_cleanup": self.cleanup_timings,
            },
            "evidence_folder_paths": {
                "mandatory": str(screenshots_root / MANDATORY_EVIDENCE_FOLDER),
                "shakedown": str(screenshots_root / SHAKEDOWN_EVIDENCE_FOLDER),
                "iat": str(screenshots_root / IAT_EVIDENCE_FOLDER),
                "silo43": str(screenshots_root / SILO43_EVIDENCE_FOLDER),
            },
            "mandatory": {
                "status": mandatory_status,
                "test_execution_order": mandatory_order_for_desktop(self.citrix_desktop_name),
                "individual_results": mandatory_payload.get("individual_results", []),
                "log_path": mandatory_payload.get("log_path"),
            },
            "shakedown": {
                "status": shakedown_status,
                "test_execution_order": SHAKEDOWN_TEST_CASE_ORDER,
                "individual_results": shakedown_payload.get("individual_results", []),
                "log_path": shakedown_payload.get("log_path"),
            },
            "iat": {
                "status": iat_status,
                "test_execution_order": IAT_TEST_CASE_ORDER,
                "individual_results": iat_results,
            },
            "silo43": {
                "status": silo43_status,
                "test_execution_order": SILO43_TEST_CASE_ORDER if is_silo43_desktop(self.citrix_desktop_name) else [],
                "individual_results": silo43_results,
            },
            "manual_check_required": manual_check_required,
            "manual_check_message": manual_check_message,
            "overall_execution_result": final_status,
            "master_steps": self.master_steps,
        }
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)
        return path

    def _generate_word_report(self, log_path: Path, final_status: str) -> Path | None:
        if final_status == "Stopped":
            self._message("Word report generation skipped because Complete Testing was stopped")
            return None
        report_started = time.perf_counter()
        try:
            report_path = generate_complete_testing_report(
                log_path=log_path,
                screenshots_base_dir=self.config.path("screenshots_dir"),
                desktop_name=self.citrix_desktop_name,
            )
            self.phase_timings["word_report_generation"] = round(time.perf_counter() - report_started, 3)
            self._attach_report_path_to_log(log_path, report_path)
            self._message(f"Word evidence report generated: {report_path}")
            return report_path
        except Exception as exc:
            self.phase_timings["word_report_generation"] = round(time.perf_counter() - report_started, 3)
            self._message(f"Word evidence report generation failed: {exc}", "ERROR")
            return None

    def _attach_report_path_to_log(self, log_path: Path, report_path: Path) -> None:
        try:
            with log_path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            payload["word_report_path"] = str(report_path)
            timings = payload.setdefault("timings_seconds", {})
            if isinstance(timings, dict):
                timings["phases"] = dict(self.phase_timings)
                timings["iat_cleanup"] = self.cleanup_timings
            with log_path.open("w", encoding="utf-8") as file:
                json.dump(payload, file, indent=2)
        except (OSError, json.JSONDecodeError) as exc:
            self._message(f"Unable to update Complete Testing log with Word report path: {exc}", "ERROR")

    def _message(self, message: str, level: str = "INFO") -> None:
        self.master_steps.append(
            {
                "timestamp": datetime.now().replace(microsecond=0).isoformat(),
                "level": level,
                "message": message,
            }
        )
        self.status_callback(message)

    def _check_stop(self) -> None:
        if self._is_stop_requested():
            raise StopRequested()
        wait_if_paused(self.pause_event, self.stop_event)
        if self._is_stop_requested():
            raise StopRequested()

    def _is_stop_requested(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()


def _read_json_log(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        payload["log_path"] = str(path)
        return payload
    except (OSError, json.JSONDecodeError):
        return {"log_path": str(path), "individual_results": []}


def _combined_result_items(
    mandatory_payload: dict,
    shakedown_payload: dict,
    iat_results: list[dict],
    post_complete_results: list[dict],
    silo43_results: list[dict] | None = None,
) -> list[dict]:
    items: list[dict] = []
    items.extend(mandatory_payload.get("individual_results", []))
    items.extend(shakedown_payload.get("individual_results", []))
    items.extend(silo43_results or [])
    items.extend(iat_results)
    items.extend(post_complete_results)
    return items


def _append_payload_results(payload: dict, results: list[dict]) -> None:
    if not results:
        return
    payload.setdefault("individual_results", [])
    payload["individual_results"].extend(results)
