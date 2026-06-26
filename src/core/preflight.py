from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.config import AppConfig
from core.evidence_audit import audit_evidence_folder
from core.execution_log import desktop_scoped_path
from core.openai_settings import get_openai_key_status
from core.test_categories import (
    IAT_EVIDENCE_FOLDER,
    MANDATORY_EVIDENCE_FOLDER,
    SHAKEDOWN_EVIDENCE_FOLDER,
    SILO43_EVIDENCE_FOLDER,
)


@dataclass
class PreflightItem:
    name: str
    status: str
    message: str


@dataclass
class PreflightResult:
    desktop_name: str
    evidence_root: str
    screenshots_root: str
    items: list[PreflightItem] = field(default_factory=list)

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.items if item.status == "Fail")

    @property
    def warning_count(self) -> int:
        return sum(1 for item in self.items if item.status == "Warning")

    @property
    def ok(self) -> bool:
        return self.failed_count == 0

    def to_dict(self) -> dict:
        return asdict(self)


def run_preflight_checks(config: AppConfig, desktop_name: str) -> PreflightResult:
    screenshots_root = desktop_scoped_path(config.path("screenshots_dir"), desktop_name)
    evidence_root = screenshots_root.parent
    result = PreflightResult(
        desktop_name=desktop_name,
        evidence_root=str(evidence_root),
        screenshots_root=str(screenshots_root),
    )

    if desktop_name.strip():
        result.items.append(PreflightItem("Desktop name", "Pass", desktop_name))
    else:
        result.items.append(PreflightItem("Desktop name", "Fail", "Citrix Desktop Name is required."))

    _check_writable_folder(result, evidence_root, "Evidence root")
    _check_writable_folder(result, screenshots_root, "Screenshots root")
    for folder_name in (
        MANDATORY_EVIDENCE_FOLDER,
        SHAKEDOWN_EVIDENCE_FOLDER,
        IAT_EVIDENCE_FOLDER,
        SILO43_EVIDENCE_FOLDER,
    ):
        _check_writable_folder(result, screenshots_root / folder_name, folder_name)

    _check_citrix_window(result, desktop_name)
    _check_ai_configuration(result, config)
    _check_report_freshness(result, config, desktop_name)
    return result


def _check_writable_folder(result: PreflightResult, folder: Path, label: str) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".preflight_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        result.items.append(PreflightItem(label, "Fail", f"Not writable: {exc}"))
    else:
        result.items.append(PreflightItem(label, "Pass", str(folder)))


def _check_citrix_window(result: PreflightResult, desktop_name: str) -> None:
    try:
        import pygetwindow as gw
    except Exception as exc:
        result.items.append(PreflightItem("Citrix window", "Warning", f"Window check unavailable: {exc}"))
        return

    normalized = desktop_name.casefold()
    try:
        matches = [title for title in gw.getAllTitles() if normalized and normalized in title.casefold()]
    except Exception as exc:
        result.items.append(PreflightItem("Citrix window", "Warning", f"Could not inspect windows: {exc}"))
        return

    if matches:
        result.items.append(PreflightItem("Citrix window", "Pass", matches[0]))
    else:
        result.items.append(
            PreflightItem(
                "Citrix window",
                "Warning",
                "No matching Citrix Desktop Viewer window is currently visible. Automation can still run after you open it.",
            )
        )


def _check_ai_configuration(result: PreflightResult, config: AppConfig) -> None:
    ai_config = config.raw.get("ai_validation", {})
    if not ai_config.get("enabled"):
        result.items.append(PreflightItem("AI validation", "Pass", "Disabled in config."))
        return

    if not ai_config.get("hostname_ip_enabled"):
        result.items.append(PreflightItem("AI validation", "Pass", "Hostname/IP AI fallback disabled."))
        return

    key_status = get_openai_key_status(ai_config)
    if key_status.configured:
        result.items.append(PreflightItem("AI validation", "Pass", key_status.detail))
    else:
        result.items.append(
            PreflightItem(
                "AI validation",
                "Warning",
                f"{key_status.detail} OCR can still run, but AI fallback will be unavailable.",
            )
        )


def _check_report_freshness(result: PreflightResult, config: AppConfig, desktop_name: str) -> None:
    try:
        audit = audit_evidence_folder(config.path("screenshots_dir"), desktop_name)
    except Exception as exc:
        result.items.append(PreflightItem("Evidence audit", "Warning", f"Audit check unavailable: {exc}"))
        return

    if not audit.items:
        result.items.append(PreflightItem("Evidence audit", "Warning", "No evidence screenshots found yet."))
        return

    if audit.report_stale:
        result.items.append(PreflightItem("Word report", "Warning", "Word report is older than the latest screenshot. Use Build Doc."))
    elif audit.report_exists:
        result.items.append(PreflightItem("Word report", "Pass", "Latest Word report is present."))
    else:
        result.items.append(PreflightItem("Word report", "Warning", "No Word report found yet."))
