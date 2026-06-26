from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

from core.config import AppConfig
from core.evidence_audit import EvidenceAuditResult, audit_evidence_folder
from core.execution_log import desktop_scoped_path, safe_folder_name
from core.run_manifest import MANIFEST_FILENAME, build_run_manifest


def create_support_bundle(
    config: AppConfig,
    desktop_name: str,
    audit_result: EvidenceAuditResult | None = None,
    max_logs: int = 20,
) -> Path:
    screenshots_root = desktop_scoped_path(config.path("screenshots_dir"), desktop_name)
    evidence_root = screenshots_root.parent
    logs_root = desktop_scoped_path(config.path("logs_dir"), desktop_name)
    bundle_root = evidence_root / "support_bundles"
    bundle_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_path = bundle_root / f"Support_Bundle_{safe_folder_name(desktop_name)}_{timestamp}.zip"
    audit = audit_result or audit_evidence_folder(config.path("screenshots_dir"), desktop_name)
    run_manifest_path, run_manifest_error = _refresh_run_manifest(config, desktop_name, evidence_root)

    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "support_bundle_manifest.json",
            json.dumps(_manifest(desktop_name, audit, run_manifest_path, run_manifest_error), indent=2),
        )
        archive.writestr("evidence_audit.json", json.dumps(audit.to_dict(), indent=2))
        if run_manifest_path is not None:
            _add_if_exists(archive, run_manifest_path, MANIFEST_FILENAME)

        _add_if_exists(archive, config.root_dir / "version.txt", "app/version.txt")
        _add_if_exists(archive, config.root_dir / "config" / "config.json", "app/config.json")

        for log_path in _latest_files(logs_root, "*.json", max_logs):
            _add_if_exists(archive, log_path, f"logs/{log_path.name}")
        for log_path in _latest_files(logs_root, "ui_execution_messages_*.txt", 5):
            _add_if_exists(archive, log_path, f"logs/{log_path.name}")

        for screenshot in _failed_screenshots(screenshots_root):
            folder = screenshot.parent.name
            _add_if_exists(archive, screenshot, f"failed_screenshots/{folder}/{screenshot.name}")

        latest_report = _latest_report(evidence_root)
        if latest_report is not None:
            _add_if_exists(archive, latest_report, f"reports/{latest_report.name}")

    return bundle_path


def _refresh_run_manifest(config: AppConfig, desktop_name: str, evidence_root: Path) -> tuple[Path | None, str | None]:
    try:
        return build_run_manifest(config.path("screenshots_dir"), config.path("logs_dir"), desktop_name), None
    except Exception as exc:
        fallback_path = evidence_root / MANIFEST_FILENAME
        return (fallback_path if fallback_path.exists() else None), str(exc)


def _manifest(
    desktop_name: str,
    audit: EvidenceAuditResult,
    run_manifest_path: Path | None,
    run_manifest_error: str | None,
) -> dict:
    return {
        "created_at": datetime.now().replace(microsecond=0).isoformat(),
        "desktop_name": desktop_name,
        "evidence_root": audit.evidence_root,
        "screenshots_root": audit.screenshots_root,
        "report_path": audit.report_path,
        "report_exists": audit.report_exists,
        "report_stale": audit.report_stale,
        "present_count": audit.present_count,
        "missing_count": audit.missing_count,
        "failed_count": audit.failed_count,
        "warning_count": audit.warning_count,
        "validation_failed_count": audit.validation_failed_count,
        "validation_warning_count": audit.validation_warning_count,
        "manual_check_count": audit.manual_check_count,
        "run_manifest_path": str(run_manifest_path) if run_manifest_path else None,
        "run_manifest_error": run_manifest_error,
    }


def _add_if_exists(archive: zipfile.ZipFile, path: Path, arcname: str) -> None:
    if path.exists() and path.is_file():
        archive.write(path, arcname)


def _latest_files(folder: Path, pattern: str, limit: int) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(folder.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _failed_screenshots(screenshots_root: Path) -> list[Path]:
    if not screenshots_root.exists():
        return []
    return sorted(screenshots_root.rglob("*_Fail_*.png"), key=lambda path: path.stat().st_mtime, reverse=True)


def _latest_report(evidence_root: Path) -> Path | None:
    if not evidence_root.exists():
        return None
    reports = sorted(evidence_root.glob("*_Testing_.docx"), key=lambda path: path.stat().st_mtime, reverse=True)
    return reports[0] if reports else None
