from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from core.execution_log import desktop_scoped_path
from core.run_manifest import MANIFEST_FILENAME
from core.test_categories import is_ring0_desktop
from core.word_report import APPLIST_SECTION_TITLE, REPORT_STRUCTURE


@dataclass
class EvidencePrefixStatus:
    prefix: str
    status: str
    latest_screenshot: str | None = None
    screenshot_count: int = 0


@dataclass
class EvidenceAuditItem:
    section_title: str
    subsection_title: str
    folder_name: str
    status: str
    prefixes: list[EvidencePrefixStatus] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class EvidenceAuditResult:
    desktop_name: str
    evidence_root: str
    screenshots_root: str
    report_path: str | None
    report_exists: bool
    report_stale: bool
    present_count: int
    missing_count: int
    failed_count: int
    warning_count: int
    validation_failed_count: int = 0
    validation_warning_count: int = 0
    manual_check_count: int = 0
    manifest_path: str | None = None
    manifest_exists: bool = False
    manifest_generated_at: str | None = None
    items: list[EvidenceAuditItem] = field(default_factory=list)
    audit_path: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.failed_count == 0
            and self.missing_count == 0
            and self.validation_failed_count == 0
            and self.manual_check_count == 0
        )

    def to_dict(self) -> dict:
        return asdict(self)


def audit_evidence_folder(screenshots_base_dir: Path, desktop_name: str) -> EvidenceAuditResult:
    screenshots_root = desktop_scoped_path(screenshots_base_dir, desktop_name)
    evidence_root = screenshots_root.parent
    report_path = _latest_report(evidence_root)
    latest_screenshot_time = _latest_screenshot_mtime(screenshots_root)
    report_exists = report_path is not None
    report_stale = bool(report_path and latest_screenshot_time and latest_screenshot_time > report_path.stat().st_mtime)

    items: list[EvidenceAuditItem] = []
    present_count = 0
    missing_count = 0
    failed_count = 0
    warning_count = 0

    for section_title, _payload_key, folder_name, subsections in REPORT_STRUCTURE:
        folder = screenshots_root / folder_name
        section_has_any_screenshot = folder.exists() and any(folder.glob("*.png"))
        if not section_has_any_screenshot:
            continue

        for subsection_title, prefixes in subsections:
            if is_ring0_desktop(desktop_name) and subsection_title == APPLIST_SECTION_TITLE:
                continue

            prefix_statuses: list[EvidencePrefixStatus] = []
            item_status = "Pass"
            notes: list[str] = []
            for prefix in prefixes:
                matches = _screenshots_for_prefix(folder, prefix)
                latest = matches[0] if matches else None
                if latest is None:
                    prefix_statuses.append(EvidencePrefixStatus(prefix=prefix, status="Missing"))
                    item_status = "Missing" if item_status == "Pass" else item_status
                    missing_count += 1
                    continue

                latest_status = "Fail" if "_Fail_" in latest.name else "Pass"
                prefix_statuses.append(
                    EvidencePrefixStatus(
                        prefix=prefix,
                        status=latest_status,
                        latest_screenshot=str(latest),
                        screenshot_count=len(matches),
                    )
                )
                present_count += 1
                if latest_status == "Fail":
                    item_status = "Fail"
                    failed_count += 1

            if prefix_statuses:
                if item_status == "Pass" and any(status.status == "Missing" for status in prefix_statuses):
                    item_status = "Partial"
                if item_status == "Partial":
                    warning_count += 1
                    notes.append("Some expected screenshots are missing.")
                items.append(
                    EvidenceAuditItem(
                        section_title=section_title,
                        subsection_title=subsection_title,
                        folder_name=folder_name,
                        status=item_status,
                        prefixes=prefix_statuses,
                        notes=notes,
                    )
                )

    if report_stale:
        warning_count += 1
    manifest_diagnostics = _manifest_diagnostics(evidence_root)
    if manifest_diagnostics["manifest_exists"]:
        items.extend(manifest_diagnostics["items"])
        warning_count += int(manifest_diagnostics["validation_warning_count"])
        warning_count += int(manifest_diagnostics["manual_check_count"])

    result = EvidenceAuditResult(
        desktop_name=desktop_name,
        evidence_root=str(evidence_root),
        screenshots_root=str(screenshots_root),
        report_path=str(report_path) if report_path else None,
        report_exists=report_exists,
        report_stale=report_stale,
        present_count=present_count,
        missing_count=missing_count,
        failed_count=failed_count,
        warning_count=warning_count,
        validation_failed_count=int(manifest_diagnostics["validation_failed_count"]),
        validation_warning_count=int(manifest_diagnostics["validation_warning_count"]),
        manual_check_count=int(manifest_diagnostics["manual_check_count"]),
        manifest_path=manifest_diagnostics["manifest_path"],
        manifest_exists=bool(manifest_diagnostics["manifest_exists"]),
        manifest_generated_at=manifest_diagnostics["manifest_generated_at"],
        items=items,
    )
    result.audit_path = str(_write_audit(result, evidence_root))
    return result


def _screenshots_for_prefix(folder: Path, prefix: str) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(folder.glob(f"{prefix}_*.png"), key=lambda path: path.stat().st_mtime, reverse=True)


def _latest_screenshot_mtime(screenshots_root: Path) -> float | None:
    if not screenshots_root.exists():
        return None
    screenshots = list(screenshots_root.rglob("*.png"))
    if not screenshots:
        return None
    return max(path.stat().st_mtime for path in screenshots)


def _latest_report(evidence_root: Path) -> Path | None:
    if not evidence_root.exists():
        return None
    reports = sorted(evidence_root.glob("*_Testing_.docx"), key=lambda path: path.stat().st_mtime, reverse=True)
    return reports[0] if reports else None


def _manifest_diagnostics(evidence_root: Path) -> dict[str, object]:
    manifest_path = evidence_root / MANIFEST_FILENAME
    empty = {
        "manifest_path": str(manifest_path) if manifest_path.exists() else None,
        "manifest_exists": manifest_path.exists(),
        "manifest_generated_at": None,
        "validation_failed_count": 0,
        "validation_warning_count": 0,
        "manual_check_count": 0,
        "items": [],
    }
    if not manifest_path.exists():
        return empty

    try:
        with manifest_path.open("r", encoding="utf-8") as file:
            manifest = json.load(file)
    except Exception as exc:
        empty["items"] = [
            EvidenceAuditItem(
                section_title="Run Manifest",
                subsection_title="Manifest Read",
                folder_name="logs",
                status="Warning",
                notes=[f"Unable to read run manifest: {exc}"],
            )
        ]
        empty["validation_warning_count"] = 1
        return empty

    testcases = manifest.get("testcases", {})
    if not isinstance(testcases, dict):
        return empty

    items: list[EvidenceAuditItem] = []
    validation_failed_count = 0
    validation_warning_count = 0
    manual_check_count = 0
    for test_case, entry in sorted(testcases.items()):
        if not isinstance(entry, dict):
            continue
        validation = entry.get("validation")
        if not isinstance(validation, dict):
            validation = {}
        requires_manual_check = bool(entry.get("requires_manual_check"))
        has_failed_validation = bool(validation.get("has_failed_validation"))
        has_warning_validation = bool(validation.get("has_warning_validation"))
        if requires_manual_check:
            manual_check_count += 1
        if has_failed_validation:
            validation_failed_count += 1
        if has_warning_validation:
            validation_warning_count += 1
        if not (requires_manual_check or has_failed_validation or has_warning_validation):
            continue

        notes: list[str] = []
        if requires_manual_check:
            notes.append(str(entry.get("manual_check_message") or "Manual check required."))
        latest_message = validation.get("latest_message")
        if isinstance(latest_message, dict):
            message = latest_message.get("message")
            if message:
                notes.append(str(message))

        status = "Fail" if has_failed_validation or requires_manual_check else "Warning"
        items.append(
            EvidenceAuditItem(
                section_title="Run Manifest",
                subsection_title=str(test_case),
                folder_name="logs",
                status=status,
                notes=notes,
            )
        )

    return {
        "manifest_path": str(manifest_path),
        "manifest_exists": True,
        "manifest_generated_at": manifest.get("generated_at"),
        "validation_failed_count": validation_failed_count,
        "validation_warning_count": validation_warning_count,
        "manual_check_count": manual_check_count,
        "items": items,
    }


def _write_audit(result: EvidenceAuditResult, evidence_root: Path) -> Path:
    logs_dir = evidence_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = logs_dir / f"Evidence_Audit_{timestamp}.json"
    with path.open("w", encoding="utf-8") as file:
        json.dump(result.to_dict(), file, indent=2)
    return path
