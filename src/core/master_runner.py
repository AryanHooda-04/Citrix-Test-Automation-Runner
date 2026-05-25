from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Callable

from core.automation_context import AutomationContext
from core.config import AppConfig
from core.evidence_replacement import remove_existing_evidence_for_prefixes
from core.execution_log import desktop_scoped_path
from core.runner import ExecutionResult, TestRunner
from core.stop_control import StopRequested, interruptible_sleep, wait_if_paused
from core.test_categories import (
    IAT_EVIDENCE_FOLDER,
    IAT_TEST_CASE_ORDER,
    MANDATORY_EVIDENCE_FOLDER,
    APPLIST_TEST_CASE_NAME,
    SHAKEDOWN_EVIDENCE_FOLDER,
    SHAKEDOWN_TEST_CASE_ORDER,
    is_success_status,
    mandatory_order_for_desktop,
)
from core.test_loader import TestCase
from core.word_report import generate_complete_testing_report


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
    report_path: Path | None = None
    manual_check_required: bool = False
    manual_check_message: str | None = None
    passed_count: int = 0
    total_count: int = 0
    duration_seconds: float = 0.0


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
    ) -> None:
        self.config = config
        self.citrix_desktop_name = citrix_desktop_name
        self.status_callback = status_callback or (lambda message: None)
        self.test_status_callback = test_status_callback or (lambda test_id, status: None)
        self.manual_confirmation_callback = manual_confirmation_callback
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.master_steps: list[dict[str, str]] = []

    def run(self, test_cases: list[TestCase]) -> MasterExecutionResult:
        started_at = datetime.now()
        tests_by_name = {test_case.name: test_case for test_case in test_cases}
        mandatory_order = mandatory_order_for_desktop(self.citrix_desktop_name)
        results = []
        stopped = False
        manual_check_required = False
        manual_check_message = None

        self._message("Starting Mandatory Testcases")
        self._message(f"Citrix Desktop Name: {self.citrix_desktop_name}")
        if APPLIST_TEST_CASE_NAME not in mandatory_order:
            self._message("Ring0 desktop detected. Applist validation will be skipped.")

        try:
            for index, test_name in enumerate(mandatory_order):
                self._check_stop()
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

                self.test_status_callback(test_case.id, "Running")
                self._message(f"Mandatory sequence running: {test_case.name}")
                result = TestRunner(
                    config=self.config,
                    citrix_desktop_name=self.citrix_desktop_name,
                    status_callback=self.status_callback,
                    stop_event=self.stop_event,
                    pause_event=self.pause_event,
                ).run(test_case)
                self.test_status_callback(test_case.id, result.status)

                results.append(self._result_to_log_entry(result))
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
                    delay = self.config.wait("mandatory_between_tests_wait_sec", 30.0)
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
        }

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
        self._message(f"Cleanup started after {test_name}")
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

        self._message(f"Cleanup completed after {test_name}")

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
            "between_tests_delay_seconds": self.config.wait("mandatory_between_tests_wait_sec", 30.0),
            "delay_confirmation": "Configured delay enforced between mandatory test cases.",
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
    ) -> None:
        self.config = config
        self.citrix_desktop_name = citrix_desktop_name
        self.status_callback = status_callback or (lambda message: None)
        self.test_status_callback = test_status_callback or (lambda test_id, status: None)
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.master_steps: list[dict[str, str]] = []

    def run(self, test_cases: list[TestCase]) -> MasterExecutionResult:
        started_at = datetime.now()
        tests_by_name = {test_case.name: test_case for test_case in test_cases}
        results = []
        stopped = False

        self._message("Starting Shakedown Testcases")
        self._message(f"Citrix Desktop Name: {self.citrix_desktop_name}")

        try:
            for index, test_name in enumerate(SHAKEDOWN_TEST_CASE_ORDER):
                self._check_stop()
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

                self.test_status_callback(test_case.id, "Running")
                self._message(f"Shakedown sequence running: {test_case.name}")
                result = TestRunner(
                    config=self.config,
                    citrix_desktop_name=self.citrix_desktop_name,
                    status_callback=self.status_callback,
                    stop_event=self.stop_event,
                    pause_event=self.pause_event,
                ).run(test_case)
                self.test_status_callback(test_case.id, result.status)

                results.append(self._result_to_log_entry(result))
                if result.status == "Stopped":
                    stopped = True
                    self._message("Shakedown Testcases stopped by user", "ERROR")
                    break

                self._check_stop()
                self._cleanup_after_test(test_case.name)

                if index < len(SHAKEDOWN_TEST_CASE_ORDER) - 1:
                    delay = self.config.wait("shakedown_between_tests_wait_sec", 10.0)
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

    def _result_to_log_entry(self, result: ExecutionResult) -> dict:
        return {
            "test_case": result.test_case_name,
            "status": result.status,
            "screenshots": [str(path) for path in result.evidence_paths],
            "log_path": str(result.log_path),
            "error": result.error_message,
        }

    def _cleanup_after_test(self, test_name: str) -> None:
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
        self._message(f"Shakedown cleanup confirmation completed after {test_name}")

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
            "between_tests_delay_seconds": self.config.wait("shakedown_between_tests_wait_sec", 10.0),
            "delay_confirmation": "Configured delay enforced between shakedown test cases.",
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
    ) -> None:
        self.config = config
        self.citrix_desktop_name = citrix_desktop_name
        self.status_callback = status_callback or (lambda message: None)
        self.test_status_callback = test_status_callback or (lambda test_id, status: None)
        self.phase_status_callback = phase_status_callback or (lambda phase, status: None)
        self.manual_confirmation_callback = manual_confirmation_callback
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.master_steps: list[dict[str, str]] = []

    def run(self, test_cases: list[TestCase]) -> CompleteExecutionResult:
        started_at = datetime.now()
        tests_by_name = {test_case.name: test_case for test_case in test_cases}
        stopped = False
        mandatory_payload: dict = {}
        shakedown_payload: dict = {}
        iat_results: list[dict] = []
        post_complete_results: list[dict] = []
        manual_check_required = False
        manual_check_message = None

        mandatory_status = "Fail"
        shakedown_status = "Fail"
        iat_status = "Fail"

        self._message("Starting Perform Complete Testing")
        self._message(f"Citrix Desktop Name: {self.citrix_desktop_name}")

        try:
            self.phase_status_callback("mandatory", "Running")
            mandatory_result = MasterRunner(
                config=self.config,
                citrix_desktop_name=self.citrix_desktop_name,
                status_callback=self.status_callback,
                test_status_callback=self.test_status_callback,
                manual_confirmation_callback=self.manual_confirmation_callback,
                stop_event=self.stop_event,
                pause_event=self.pause_event,
            ).run(test_cases)
            mandatory_status = mandatory_result.status
            self.phase_status_callback("mandatory", mandatory_status)
            mandatory_payload = _read_json_log(mandatory_result.log_path)
            stopped = mandatory_status == "Stopped"
            manual_check_required = mandatory_result.manual_check_required
            manual_check_message = mandatory_result.manual_check_message

            if manual_check_required:
                shakedown_status = "Skipped"
                iat_status = "Skipped"
                self.phase_status_callback("shakedown", shakedown_status)
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
                phase_delay = self.config.wait("complete_phase_transition_wait_sec", 10.0)
                self._message(f"Complete Testing delay before Shakedown: {phase_delay} second(s)")
                _sleep(phase_delay, self.stop_event, self.pause_event)

                self.phase_status_callback("shakedown", "Running")
                shakedown_result = ShakedownRunner(
                    config=self.config,
                    citrix_desktop_name=self.citrix_desktop_name,
                    status_callback=self.status_callback,
                    test_status_callback=self.test_status_callback,
                    stop_event=self.stop_event,
                    pause_event=self.pause_event,
                ).run(test_cases)
                shakedown_status = shakedown_result.status
                self.phase_status_callback("shakedown", shakedown_status)
                shakedown_payload = _read_json_log(shakedown_result.log_path)
                stopped = shakedown_status == "Stopped"

            if not stopped and not manual_check_required:
                self._check_stop()
                phase_delay = self.config.wait("complete_phase_transition_wait_sec", 10.0)
                self._message(f"Complete Testing delay before IAT: {phase_delay} second(s)")
                _sleep(phase_delay, self.stop_event, self.pause_event)

                self.phase_status_callback("iat", "Running")
                iat_results = self._run_iat_tests(tests_by_name)
                iat_status = "Pass" if iat_results and all(is_success_status(item["status"]) for item in iat_results) else "Fail"
                self.phase_status_callback("iat", iat_status)

            if not stopped and not manual_check_required:
                self._check_stop()
                self.phase_status_callback("post_complete", "Running")
                post_complete_result = self._capture_post_complete_zscaler_evidence()
                post_complete_results.append(post_complete_result)
                self.phase_status_callback("post_complete", post_complete_result["status"])
                _append_payload_results(mandatory_payload, post_complete_results)
        except StopRequested:
            stopped = True
            self._message("Perform Complete Testing stopped by user", "ERROR")

        ended_at = datetime.now()
        phase_statuses = [mandatory_status, shakedown_status, iat_status]
        failed_count = sum(1 for status in phase_statuses if not is_success_status(status))
        failed_count += sum(1 for item in post_complete_results if not is_success_status(item.get("status", "Fail")))
        result_items = _combined_result_items(mandatory_payload, shakedown_payload, iat_results, post_complete_results)
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
            mandatory_status,
            shakedown_status,
            iat_status,
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
            report_path=report_path,
            manual_check_required=manual_check_required,
            manual_check_message=manual_check_message,
            passed_count=passed_count,
            total_count=total_count,
            duration_seconds=duration_seconds,
        )

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

            self.test_status_callback(test_case.id, "Running")
            self._message(f"IAT sequence running: {test_case.name}")
            result = TestRunner(
                config=self.config,
                citrix_desktop_name=self.citrix_desktop_name,
                status_callback=self.status_callback,
                stop_event=self.stop_event,
                pause_event=self.pause_event,
            ).run(test_case)
            self.test_status_callback(test_case.id, result.status)
            results.append(
                {
                    "test_case": result.test_case_name,
                    "status": result.status,
                    "screenshots": [str(path) for path in result.evidence_paths],
                    "log_path": str(result.log_path),
                    "error": result.error_message,
                }
            )
            self._cleanup_after_iat(test_case.name)
        return results

    def _cleanup_after_iat(self, test_name: str) -> None:
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
        self._message(f"IAT cleanup confirmation completed after {test_name}")

    def _capture_post_complete_zscaler_evidence(self) -> dict:
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
            stop_event=self.stop_event,
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
            context.step("Open Windows Search using Windows + S")
            context.hotkey("winleft", "s")
            context.wait(self.config.wait("windows_search_wait_sec", 2.0))
            context.step("Search and launch application: Apps: ZCCVDI")
            context.type_text("Apps: ZCCVDI", interval=0.15)
            context.wait(self.config.wait("windows_search_results_wait_sec", 5.0))
            context.press("enter")
            context.step("Wait for Zscaler Client Connector VDI application to open")
            context.wait(self.config.wait("zscaler_launch_wait_sec", 20.0))
            screenshot_path = context.capture_evidence("zscaler_services_2")
            status = "Pass"
            self._message(f"Post-complete ZScaler evidence captured: {screenshot_path}")
        except StopRequested:
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
            "test_case": "Zscaler_Services_Evidence_Post_Complete",
            "status": status,
            "screenshots": [str(screenshot_path)] if screenshot_path else [],
            "log_path": None,
            "error": error,
            "capture_timing": "After final IAT testcase during Perform Complete Testing",
        }

    def _write_complete_log(
        self,
        started_at: datetime,
        ended_at: datetime,
        final_status: str,
        mandatory_payload: dict,
        shakedown_payload: dict,
        iat_results: list[dict],
        mandatory_status: str,
        shakedown_status: str,
        iat_status: str,
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
            "phase_transition_delay_seconds": self.config.wait("complete_phase_transition_wait_sec", 10.0),
            "evidence_folder_paths": {
                "mandatory": str(screenshots_root / MANDATORY_EVIDENCE_FOLDER),
                "shakedown": str(screenshots_root / SHAKEDOWN_EVIDENCE_FOLDER),
                "iat": str(screenshots_root / IAT_EVIDENCE_FOLDER),
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
        try:
            report_path = generate_complete_testing_report(
                log_path=log_path,
                screenshots_base_dir=self.config.path("screenshots_dir"),
                desktop_name=self.citrix_desktop_name,
            )
            self._attach_report_path_to_log(log_path, report_path)
            self._message(f"Word evidence report generated: {report_path}")
            return report_path
        except Exception as exc:
            self._message(f"Word evidence report generation failed: {exc}", "ERROR")
            return None

    def _attach_report_path_to_log(self, log_path: Path, report_path: Path) -> None:
        try:
            with log_path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
            payload["word_report_path"] = str(report_path)
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
) -> list[dict]:
    items: list[dict] = []
    items.extend(mandatory_payload.get("individual_results", []))
    items.extend(shakedown_payload.get("individual_results", []))
    items.extend(iat_results)
    items.extend(post_complete_results)
    return items


def _append_payload_results(payload: dict, results: list[dict]) -> None:
    if not results:
        return
    payload.setdefault("individual_results", [])
    payload["individual_results"].extend(results)
