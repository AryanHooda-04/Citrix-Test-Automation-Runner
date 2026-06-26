from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from core.execution_log import desktop_scoped_path
from core.test_categories import (
    IAT_TEST_CASE_ORDER,
    MANDATORY_TEST_CASE_ORDER,
    POST_COMPLETE_ZSCALER_TEST_NAME,
    SHAKEDOWN_TEST_CASE_ORDER,
    SILO43_TEST_CASE_ORDER,
    evidence_category_for_test_name,
    is_success_status,
)
from core.word_report import REPORT_STRUCTURE


MANIFEST_FILENAME = "run_manifest.json"

TESTCASE_SCREENSHOT_PREFIXES = {
    "Hostname_and_IP_Evidence": ["Hostname_and_IP_Evidence"],
    "Edge_WebView_Version_Evidence": ["webview_evidence"],
    "Edge_Browser_Version_Evidence": ["edge_evidence"],
    "Zscaler_Services_Evidence": ["zscaler_evidence"],
    POST_COMPLETE_ZSCALER_TEST_NAME: ["zscaler_services_2"],
    "Google_and_Yahoo_Web_Access_Evidence": ["google_evidence", "yahoo_evidence"],
    "Office_Applications_Launch": ["powerpnt_evidence"],
    "Applist_Validation_Evidence": ["applist_evidence"],
    "Shakedown_Desktop_Availability_Evidence": ["desktop_availability"],
    "Shakedown_OneDrive_Sync_Evidence": ["onedrive_sync"],
    "Shakedown_Edge_Sync_Evidence": ["edge_sync", "edge_browser_version"],
    "Shakedown_Edge_Policy_PAC_Evidence": ["policy_pac_1", "policy_pac_2"],
    "Shakedown_Windows_Version_Evidence": ["winver"],
    "Shakedown_Local_Network_Drives_Evidence": [
        "local_network_drives",
        "local_network_drives_deleted",
    ],
    "Shakedown_FSLogix_Profile_Log_Evidence": ["fslogix_profile_log"],
    "Shakedown_Temp_Folder_Evidence": ["temp_files"],
    "IAT_Core_Application_Test_Evidence": [
        "7-zip_evidence",
        "adobe_acrobat_evidence",
        "Microsoft_Office_evidence",
        "Microsoft_Visio_evidence",
        "Microsoft_Project_evidence",
        "citrix_vda_evidence",
        "OpenJDK_JRE_evidence",
        "fslogix_apps_evidence",
    ],
    "Silo43_Oracle_12_Bin_Path_Evidence": ["silo43_oracle_12_bin_path_evidence"],
    "Silo43_Nice_Env_Variables_Evidence": ["silo43_oracle_12_bin_path_evidence"],
    "Silo43_VLS_Privilege_Warning_Evidence": ["silo43_vls_privilege_warning_evidence"],
    "Silo43_Ping_Prod_DVFS_Evidence": ["silo43_ping_prod_dvfs_evidence"],
    "Silo43_BAD_Folder_Evidence": ["silo43_bad_folder_evidence"],
}


def build_run_manifest(screenshots_base_dir: Path, logs_base_dir: Path, desktop_name: str) -> Path:
    screenshots_root = desktop_scoped_path(screenshots_base_dir, desktop_name)
    logs_root = desktop_scoped_path(logs_base_dir, desktop_name)
    evidence_root = screenshots_root.parent
    evidence_root.mkdir(parents=True, exist_ok=True)

    screenshot_index = _index_screenshots(screenshots_root)
    latest_results = _latest_results_from_logs(logs_root)
    latest_report = _latest_report_path(evidence_root)
    testcases = _build_testcase_entries(latest_results, screenshot_index)
    sections = _build_section_entries(screenshot_index)

    manifest = {
        "schema_version": 1,
        "generated_at": _now_iso(),
        "desktop_name": desktop_name,
        "evidence_root": str(evidence_root),
        "screenshots_root": str(screenshots_root),
        "logs_root": str(logs_root),
        "latest_report_path": str(latest_report) if latest_report else None,
        "summary": _summary(testcases, screenshot_index),
        "testcases": testcases,
        "sections": sections,
    }

    manifest_path = evidence_root / MANIFEST_FILENAME
    with manifest_path.open("w", encoding="utf-8") as file:
        json.dump(manifest, file, indent=2)
    return manifest_path


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _index_screenshots(screenshots_root: Path) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    if not screenshots_root.exists():
        return index

    for image_path in screenshots_root.rglob("*.png"):
        prefix = _matched_prefix(image_path.name)
        if not prefix:
            continue
        stat = image_path.stat()
        item = {
            "path": str(image_path),
            "folder": image_path.parent.name,
            "prefix": prefix,
            "filename": image_path.name,
            "status_from_filename": _status_from_filename(image_path.name),
            "modified_at": datetime.fromtimestamp(stat.st_mtime).replace(microsecond=0).isoformat(),
            "modified_timestamp": stat.st_mtime,
        }
        index.setdefault(prefix, []).append(item)

    for items in index.values():
        items.sort(key=lambda item: item.get("modified_timestamp", 0), reverse=True)
    return index


def _matched_prefix(filename: str) -> str | None:
    name = filename.casefold()
    for prefixes in TESTCASE_SCREENSHOT_PREFIXES.values():
        for prefix in prefixes:
            if name.startswith(prefix.casefold()):
                return prefix
    return None


def _status_from_filename(filename: str) -> str | None:
    name = filename.casefold()
    if "_fail_" in name:
        return "Fail"
    if "_pass_" in name:
        return "Pass"
    return None


def _latest_results_from_logs(logs_root: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not logs_root.exists():
        return latest

    for log_path in logs_root.rglob("*.json"):
        try:
            with log_path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError):
            continue
        fallback_timestamp = log_path.stat().st_mtime
        for result in _iter_result_payloads(payload):
            test_case = result.get("test_case")
            status = result.get("status")
            if not test_case or not status:
                continue
            timestamp = _result_timestamp(result, fallback_timestamp)
            candidate = {
                "test_case": str(test_case),
                "status": str(status),
                "seen_at": datetime.fromtimestamp(timestamp).replace(microsecond=0).isoformat(),
                "seen_timestamp": timestamp,
                "source_log_path": str(result.get("log_path") or log_path),
                "suite_log_path": str(log_path),
                "error": result.get("error"),
                "metadata": result.get("metadata") if isinstance(result.get("metadata"), dict) else {},
                "requires_manual_check": bool(result.get("requires_manual_check")),
                "manual_check_message": result.get("manual_check_message"),
                "screenshots": _result_screenshots(result),
                "validation": _validation_diagnostics(result),
            }
            current = latest.get(str(test_case))
            if current is None or timestamp >= float(current.get("seen_timestamp", 0)):
                latest[str(test_case)] = candidate
    return latest


def _iter_result_payloads(value: Any):
    if isinstance(value, dict):
        if value.get("test_case") and value.get("status"):
            yield value
        for key in ("individual_results", "results", "test_results"):
            nested = value.get(key)
            if isinstance(nested, list):
                for item in nested:
                    yield from _iter_result_payloads(item)
        for key in ("mandatory", "shakedown", "silo43", "iat", "post_complete", "phases"):
            nested = value.get(key)
            if isinstance(nested, (dict, list)):
                yield from _iter_result_payloads(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_result_payloads(item)


def _result_timestamp(result: dict[str, Any], fallback_timestamp: float) -> float:
    for key in ("end_time", "completed_at", "start_time"):
        raw_value = result.get(key)
        if isinstance(raw_value, str):
            try:
                return datetime.fromisoformat(raw_value).timestamp()
            except ValueError:
                continue
    return fallback_timestamp


def _result_screenshots(result: dict[str, Any]) -> list[str]:
    screenshots: list[str] = []
    for key in ("screenshots", "evidence_screenshots"):
        raw_value = result.get(key)
        if isinstance(raw_value, list):
            screenshots.extend(str(path) for path in raw_value if path)
    for key in ("screenshot", "manual_confirmation_screenshot"):
        raw_value = result.get(key)
        if raw_value:
            screenshots.append(str(raw_value))
    return list(dict.fromkeys(screenshots))


def _validation_diagnostics(result: dict[str, Any]) -> dict[str, Any]:
    steps = result.get("steps")
    if not isinstance(steps, list):
        return _empty_validation_diagnostics()

    messages = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        message = step.get("message")
        if not isinstance(message, str) or not _is_validation_message(message):
            continue
        messages.append(
            {
                "timestamp": step.get("timestamp"),
                "level": str(step.get("level") or "INFO"),
                "message": message,
                "outcome": _validation_outcome(message, step.get("level")),
            }
        )

    failed_messages = [item for item in messages if item["outcome"] == "failed"]
    warning_messages = [item for item in messages if item["outcome"] == "warning"]
    passed_messages = [item for item in messages if item["outcome"] == "passed"]
    latest_failed_index = _latest_message_index(messages, "failed")
    latest_passed_index = _latest_message_index(messages, "passed")
    has_active_failed_validation = bool(failed_messages) and (
        latest_passed_index == -1 or latest_failed_index > latest_passed_index
    )
    has_recovered_validation_failure = bool(failed_messages) and latest_passed_index > latest_failed_index
    return {
        "message_count": len(messages),
        "has_validation_messages": bool(messages),
        "has_failed_validation": has_active_failed_validation,
        "has_active_failed_validation": has_active_failed_validation,
        "has_recovered_validation_failure": has_recovered_validation_failure,
        "has_warning_validation": bool(warning_messages),
        "latest_message": messages[-1] if messages else None,
        "passed_messages": passed_messages,
        "warning_messages": warning_messages,
        "failed_messages": failed_messages,
    }


def _empty_validation_diagnostics() -> dict[str, Any]:
    return {
        "message_count": 0,
        "has_validation_messages": False,
        "has_failed_validation": False,
        "has_active_failed_validation": False,
        "has_recovered_validation_failure": False,
        "has_warning_validation": False,
        "latest_message": None,
        "passed_messages": [],
        "warning_messages": [],
        "failed_messages": [],
    }


def _latest_message_index(messages: list[dict[str, Any]], outcome: str) -> int:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].get("outcome") == outcome:
            return index
    return -1


def _is_validation_message(message: str) -> bool:
    lowered = message.casefold()
    markers = (
        "validation",
        "ocr ",
        "ocr:",
        "ai ",
        "ai:",
        "openai",
        "could not read",
        "not available",
        "not ok",
        "connection error",
    )
    return any(marker in lowered for marker in markers)


def _validation_outcome(message: str, level: object) -> str:
    lowered = message.casefold()
    if str(level or "").upper() == "ERROR":
        return "failed"
    if str(level or "").upper() == "WARNING":
        return "warning"
    if "warning" in lowered or "ocr failed" in lowered or "switching to ai" in lowered:
        return "warning"
    if "validation passed" in lowered or "ai validation passed" in lowered:
        return "passed"
    if (
        "validation failed" in lowered
        or "validation error" in lowered
        or "could not read" in lowered
        or "failed:" in lowered
    ):
        return "failed"
    return "info"


def _build_testcase_entries(
    latest_results: dict[str, dict[str, Any]],
    screenshot_index: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    ordered_testcases = [
        *MANDATORY_TEST_CASE_ORDER,
        POST_COMPLETE_ZSCALER_TEST_NAME,
        *SHAKEDOWN_TEST_CASE_ORDER,
        *IAT_TEST_CASE_ORDER,
        *SILO43_TEST_CASE_ORDER,
    ]
    for test_case in ordered_testcases:
        prefixes = TESTCASE_SCREENSHOT_PREFIXES.get(test_case, [])
        screenshots = _latest_screenshots_for_prefixes(prefixes, screenshot_index)
        latest_result = latest_results.get(test_case, {})
        screenshot_status = _status_from_screenshots(screenshots)
        status = screenshot_status or latest_result.get("status")
        if not status and not screenshots:
            continue
        error = latest_result.get("error")
        validation = latest_result.get("validation", _empty_validation_diagnostics())
        if screenshot_status and is_success_status(str(screenshot_status)):
            error = None
            validation = _validation_recovered_by_latest_screenshot(validation)
        entries[test_case] = {
            "status": status or "Unknown",
            "phase": _phase_for_test_case(test_case),
            "evidence_category": evidence_category_for_test_name(test_case),
            "latest_log_path": latest_result.get("source_log_path"),
            "suite_log_path": latest_result.get("suite_log_path"),
            "latest_seen_at": latest_result.get("seen_at"),
            "error": error,
            "requires_manual_check": bool(latest_result.get("requires_manual_check")),
            "manual_check_message": latest_result.get("manual_check_message"),
            "metadata": latest_result.get("metadata", {}),
            "validation": validation,
            "expected_prefixes": prefixes,
            "latest_screenshots": screenshots,
        }
    return entries


def _latest_screenshots_for_prefixes(
    prefixes: list[str],
    screenshot_index: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    screenshots = []
    for prefix in prefixes:
        latest = (screenshot_index.get(prefix) or [])[:1]
        screenshots.extend(_public_screenshot_item(item) for item in latest)
    return screenshots


def _public_screenshot_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": item["path"],
        "folder": item["folder"],
        "prefix": item["prefix"],
        "filename": item["filename"],
        "status_from_filename": item["status_from_filename"],
        "modified_at": item["modified_at"],
    }


def _status_from_screenshots(screenshots: list[dict[str, Any]]) -> str | None:
    statuses = {item.get("status_from_filename") for item in screenshots}
    if "Fail" in statuses:
        return "Fail"
    if "Pass" in statuses:
        return "Pass"
    return None


def _validation_recovered_by_latest_screenshot(validation: Any) -> dict[str, Any]:
    if not isinstance(validation, dict):
        return _empty_validation_diagnostics()
    recovered = dict(validation)
    recovered["has_failed_validation"] = False
    recovered["has_active_failed_validation"] = False
    recovered["has_recovered_validation_failure"] = bool(validation.get("has_failed_validation"))
    return recovered


def _phase_for_test_case(test_case: str) -> str:
    if test_case in MANDATORY_TEST_CASE_ORDER:
        return "mandatory"
    if test_case == POST_COMPLETE_ZSCALER_TEST_NAME:
        return "post_complete"
    if test_case in SHAKEDOWN_TEST_CASE_ORDER:
        return "shakedown"
    if test_case in SILO43_TEST_CASE_ORDER:
        return "silo43"
    return "iat"


def _build_section_entries(screenshot_index: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    sections = []
    for section_title, payload_key, folder_name, subsections in REPORT_STRUCTURE:
        section = {
            "title": section_title,
            "phase": payload_key,
            "folder": folder_name,
            "subsections": [],
        }
        for subsection_title, prefixes in subsections:
            images = _latest_screenshots_for_prefixes(prefixes, screenshot_index)
            section["subsections"].append(
                {
                    "title": subsection_title,
                    "prefixes": prefixes,
                    "latest_screenshots": images,
                    "included_in_report": bool(images),
                }
            )
        sections.append(section)
    return sections


def _latest_report_path(evidence_root: Path) -> Path | None:
    reports = sorted(
        evidence_root.glob("*_Testing_.docx"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return reports[0] if reports else None


def _summary(
    testcases: dict[str, dict[str, Any]],
    screenshot_index: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    statuses = [entry.get("status") for entry in testcases.values()]
    screenshot_count = sum(len(items) for items in screenshot_index.values())
    latest_screenshot_count = sum(len(entry.get("latest_screenshots", [])) for entry in testcases.values())
    validations = [
        entry.get("validation")
        for entry in testcases.values()
        if isinstance(entry.get("validation"), dict)
    ]
    return {
        "testcases_with_evidence_or_logs": len(testcases),
        "passed": sum(1 for status in statuses if is_success_status(str(status))),
        "failed": sum(1 for status in statuses if status == "Fail"),
        "skipped": sum(1 for status in statuses if status == "Skipped"),
        "unknown": sum(1 for status in statuses if status in {None, "Unknown"}),
        "validation_message_testcases": sum(
            1 for validation in validations if validation.get("has_validation_messages")
        ),
        "validation_failed_testcases": sum(
            1 for validation in validations if validation.get("has_active_failed_validation", validation.get("has_failed_validation"))
        ),
        "validation_recovered_testcases": sum(
            1 for validation in validations if validation.get("has_recovered_validation_failure")
        ),
        "validation_warning_testcases": sum(
            1 for validation in validations if validation.get("has_warning_validation")
        ),
        "screenshots_total": screenshot_count,
        "latest_screenshots_indexed": latest_screenshot_count,
        "failed_screenshots_total": sum(
            1
            for items in screenshot_index.values()
            for item in items
            if item.get("status_from_filename") == "Fail"
        ),
    }
