from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable

from core.automation_context import AutomationContext, evidence_category_path
from core.config import AppConfig
from core.evidence_replacement import remove_existing_evidence_for_test_case
from core.execution_log import ExecutionLog, desktop_scoped_path
from core.screenshot import ScreenshotManager
from core.stop_control import StopRequested
from core.test_categories import evidence_category_for_test_name, should_skip_test_for_desktop
from core.test_loader import TestCase


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    test_case_name: str
    log_path: Path
    screenshot_path: Path | None
    evidence_paths: tuple[Path, ...] = ()
    error_message: str | None = None


class TestRunner:
    def __init__(
        self,
        config: AppConfig,
        citrix_desktop_name: str,
        status_callback: Callable[[str], None] | None = None,
        stop_event: Event | None = None,
        pause_event: Event | None = None,
    ) -> None:
        self.config = config
        self.citrix_desktop_name = citrix_desktop_name
        self.status_callback = status_callback or (lambda message: None)
        self.stop_event = stop_event
        self.pause_event = pause_event

    def run(self, test_case: TestCase) -> ExecutionResult:
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
            execution_log.add_step(
                "Skipped because Citrix Desktop Name contains Ring0; Applist validation is not required.",
                "INFO",
            )
            self.status_callback("Applist validation skipped for Ring0 desktop.")
            log_path = execution_log.finish("Skipped", None, [])
            return ExecutionResult("Skipped", test_case.name, log_path, None, ())

        def add_step(message: str, level: str = "INFO") -> None:
            execution_log.add_step(message, level)
            self.status_callback(message)

        deleted_count = remove_existing_evidence_for_test_case(
            screenshots_dir,
            test_case,
            lambda message: add_step(message, "INFO"),
        )
        if deleted_count:
            add_step(f"Previous evidence replaced for this testcase: {deleted_count} screenshot(s) removed")

        context = AutomationContext(
            config=self.config,
            log_step=add_step,
            citrix_desktop_name=self.citrix_desktop_name,
            evidence_category=evidence_category,
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
        screenshot_path: Path | None = None

        try:
            add_step(f"Starting test case: {test_case.name}")
            add_step(f"Desktop name entered by user: {self.citrix_desktop_name}")
            test_case.run(context)
            screenshots.capture_region = context.capture_region()
            add_step("Automation script completed successfully")

            if test_case.capture_screenshot and self.config.screenshot_settings.get("capture_on_pass", True):
                screenshot_name = test_case.evidence_name or test_case.name
                screenshot_path = screenshots.capture(screenshot_name, "Pass")
                context.evidence_paths.append(screenshot_path)
                add_step(f"Pass screenshot saved: {screenshot_path}")

                if self.config.screenshot_settings.get("copy_pass_screenshot_to_clipboard", True):
                    screenshots.copy_to_clipboard(screenshot_path)
                    add_step("Pass screenshot copied to clipboard")

            log_path = execution_log.finish("Pass", screenshot_path, context.evidence_paths)
            return ExecutionResult("Pass", test_case.name, log_path, screenshot_path, tuple(context.evidence_paths))

        except StopRequested:
            add_step("Execution stopped by user", "ERROR")
            log_path = execution_log.finish("Stopped", screenshot_path, context.evidence_paths)
            return ExecutionResult("Stopped", test_case.name, log_path, screenshot_path, tuple(context.evidence_paths))

        except BaseException as exc:
            execution_log.set_error(exc)
            self.status_callback(f"Failure: {exc}")
            screenshots.capture_region = context.capture_region()

            if test_case.capture_screenshot and self.config.screenshot_settings.get("capture_on_fail", True):
                try:
                    screenshot_name = test_case.evidence_name or test_case.name
                    screenshot_path = screenshots.capture(screenshot_name, "Fail")
                    context.evidence_paths.append(screenshot_path)
                    execution_log.add_step(f"Failure screenshot saved: {screenshot_path}", "ERROR")
                    if self.config.screenshot_settings.get("copy_fail_screenshot_to_clipboard", False):
                        screenshots.copy_to_clipboard(screenshot_path)
                        execution_log.add_step("Failure screenshot copied to clipboard", "ERROR")
                except BaseException as screenshot_error:
                    execution_log.add_step(
                        f"Unable to capture failure screenshot: {screenshot_error}",
                        "ERROR",
                    )

            log_path = execution_log.finish("Fail", screenshot_path, context.evidence_paths)
            return ExecutionResult("Fail", test_case.name, log_path, screenshot_path, tuple(context.evidence_paths), str(exc))
