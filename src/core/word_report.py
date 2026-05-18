from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from PIL import Image

from core.execution_log import desktop_scoped_path, safe_filename, safe_folder_name
from core.test_categories import (
    IAT_EVIDENCE_FOLDER,
    MANDATORY_EVIDENCE_FOLDER,
    SHAKEDOWN_EVIDENCE_FOLDER,
    is_success_status,
)


REPORT_STRUCTURE = [
    (
        "Mandatory Testcases",
        "mandatory",
        MANDATORY_EVIDENCE_FOLDER,
        [
            ("Hostname & IP address", ["Hostname_and_IP_Evidence"]),
            ("Edge & Edge WebView Versions", ["edge_evidence", "webview_evidence"]),
            ("ZScaler Services", ["zscaler_evidence", "zscaler_services_2"]),
            ("Office Applications Launch", ["excel_evidence", "word_evidence", "powerpnt_evidence"]),
            ("Google and Yahoo Web Access", ["google_evidence", "yahoo_evidence"]),
            ("APP List (if applicable)", ["applist_evidence"]),
        ],
    ),
    (
        "SD: Shakedown Testcases",
        "shakedown",
        SHAKEDOWN_EVIDENCE_FOLDER,
        [
            ("Desktop Availability", ["desktop_availability"]),
            ("OneDrive Sync", ["onedrive_sync"]),
            ("Edge Sync", ["edge_sync", "edge_browser_version"]),
            ("Proxy PAC", ["policy_pac_1", "policy_pac_2"]),
            ("Windows Version", ["winver"]),
            ("Local and Network Drives", ["local_network_drives", "local_network_drives_deleted"]),
            ("FSLogix Profile Log Check", ["fslogix_profile_log"]),
            ("Verification of Unnecessary Files in C:\\Temp", ["temp_files"]),
        ],
    ),
    (
        "IAT: Core Applications Test",
        "iat",
        IAT_EVIDENCE_FOLDER,
        [
            ("7-Zip", ["7-zip_evidence"]),
            ("Adobe Acrobat Reader", ["adobe_acrobat_evidence"]),
            ("Microsoft Office", ["Microsoft_Office_evidence"]),
            ("Microsoft Visio", ["Microsoft_Visio_evidence"]),
            ("Microsoft Project", ["Microsoft_Project_evidence"]),
            ("Citrix VDA", ["citrix_vda_evidence"]),
            ("OpenJDK / JRE", ["OpenJDK_JRE_evidence"]),
            ("FSLogix", ["fslogix_apps_evidence"]),
        ],
    ),
]


def generate_complete_testing_report(log_path: Path, screenshots_base_dir: Path, desktop_name: str) -> Path:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt

    payload = _read_payload(log_path)
    screenshots_root = desktop_scoped_path(screenshots_base_dir, desktop_name)
    evidence_root = screenshots_root.parent
    evidence_root.mkdir(parents=True, exist_ok=True)
    report_path = evidence_root / f"{safe_folder_name(desktop_name)}_Testing_.docx"

    doc = Document()
    _configure_document(doc, Pt, Inches)
    max_width_inches = _content_width_inches(doc)

    title = doc.add_paragraph()
    title_format = title.paragraph_format
    title_format.space_after = Pt(12)
    title_run = title.add_run(f"{desktop_name} Testing:")
    title_run.bold = True
    title_run.font.name = "Calibri"
    title_run.font.size = Pt(14)

    failures = _failure_entries(payload)
    if failures:
        _add_failure_summary(doc, failures, Pt, WD_TABLE_ALIGNMENT)

    for section_title, payload_key, _folder_name, subsections in REPORT_STRUCTURE:
        _add_section_heading(doc, section_title, Pt)
        phase_paths = _phase_screenshots(payload, payload_key)
        folder_paths = _folder_screenshots(screenshots_root / _folder_name)
        for subsection_title, prefixes in subsections:
            images = _matching_images([*phase_paths, *folder_paths], prefixes)
            if not images and subsection_title == "APP List (if applicable)":
                continue
            _add_subsection_heading(doc, subsection_title, Pt)
            if images:
                for image_path in images:
                    _add_image(doc, image_path, max_width_inches, Inches, WD_ALIGN_PARAGRAPH)
            else:
                _add_missing_note(doc, Pt)

    doc.save(report_path)
    return report_path


def _read_payload(log_path: Path) -> dict:
    with log_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _configure_document(doc, Pt, Inches) -> None:
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    section = doc.sections[0]
    section.top_margin = Inches(0.7)
    section.bottom_margin = Inches(0.7)
    section.left_margin = Inches(0.65)
    section.right_margin = Inches(0.65)


def _content_width_inches(doc) -> float:
    section = doc.sections[0]
    content_width_emu = section.page_width - section.left_margin - section.right_margin
    return content_width_emu / 914400


def _add_section_heading(doc, text: str, Pt) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(12)
    paragraph.paragraph_format.space_after = Pt(8)
    run = paragraph.add_run(text)
    run.bold = True
    run.font.name = "Calibri"
    run.font.size = Pt(13)


def _add_subsection_heading(doc, text: str, Pt) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(8)
    paragraph.paragraph_format.space_after = Pt(4)
    run = paragraph.add_run(text)
    run.bold = True
    run.font.name = "Calibri"
    run.font.size = Pt(11)


def _add_missing_note(doc, Pt) -> None:
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(8)
    run = paragraph.add_run("No screenshot captured in this execution.")
    run.italic = True
    run.font.name = "Calibri"
    run.font.size = Pt(10)


def _add_failure_summary(doc, failures: list[dict[str, str]], Pt, WD_TABLE_ALIGNMENT) -> None:
    _add_section_heading(doc, "Failure Summary", Pt)
    table = doc.add_table(rows=1, cols=5)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    headers = ["Section", "Testcase", "Status", "Error / Log Path", "Screenshots"]
    for cell, header in zip(table.rows[0].cells, headers):
        paragraph = cell.paragraphs[0]
        run = paragraph.add_run(header)
        run.bold = True
        run.font.name = "Calibri"
        run.font.size = Pt(10)

    for failure in failures:
        row = table.add_row().cells
        values = [
            failure["section"],
            failure["test_case"],
            failure["status"],
            failure["detail"],
            failure["screenshots"],
        ]
        for cell, value in zip(row, values):
            paragraph = cell.paragraphs[0]
            run = paragraph.add_run(value)
            run.font.name = "Calibri"
            run.font.size = Pt(9)
    doc.add_paragraph()


def _add_image(doc, image_path: Path, max_width_inches: float, Inches, WD_ALIGN_PARAGRAPH) -> None:
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_after = Inches(0.12)
    width_inches = _fit_image_width_inches(image_path, max_width_inches)
    run = paragraph.add_run()
    run.add_picture(str(image_path), width=Inches(width_inches))
    doc.add_paragraph()


def _fit_image_width_inches(image_path: Path, max_width_inches: float) -> float:
    try:
        with Image.open(image_path) as image:
            dpi = image.info.get("dpi", (96, 96))[0] or 96
            native_width_inches = image.width / dpi
    except OSError:
        native_width_inches = max_width_inches
    return min(native_width_inches, max_width_inches)


def _phase_screenshots(payload: dict, phase_key: str) -> list[Path]:
    phase = payload.get(phase_key, {})
    results = phase.get("individual_results", [])
    paths: list[Path] = []
    for result in results:
        for value in result.get("screenshots", []):
            path = Path(value)
            if path.exists():
                paths.append(path)
    return paths


def _folder_screenshots(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        (path for path in folder.glob("*.png") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _failure_entries(payload: dict) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    labels = {
        "mandatory": "Mandatory",
        "shakedown": "Shakedown",
        "iat": "IAT",
    }
    for phase_key, label in labels.items():
        phase = payload.get(phase_key, {})
        for result in phase.get("individual_results", []):
            status = result.get("status", "Unknown")
            if is_success_status(status):
                continue
            screenshots = [str(value) for value in result.get("screenshots", [])]
            detail_parts = []
            if result.get("error"):
                detail_parts.append(str(result["error"]))
            if result.get("log_path"):
                detail_parts.append(str(result["log_path"]))
            entries.append(
                {
                    "section": label,
                    "test_case": str(result.get("test_case", "Unknown")),
                    "status": str(status),
                    "detail": "\n".join(detail_parts) if detail_parts else "-",
                    "screenshots": "\n".join(screenshots) if screenshots else "-",
                }
            )
    return entries


def _matching_images(paths: Iterable[Path], prefixes: list[str]) -> list[Path]:
    selected: list[Path] = []
    unique_paths = []
    seen = set()
    for path in paths:
        resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(path)

    for prefix in prefixes:
        matches = sorted(
            (path for path in unique_paths if _matches_evidence_prefix(path, prefix)),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if matches:
            selected.append(matches[0])
    return selected


def _matches_evidence_prefix(path: Path, prefix: str) -> bool:
    safe_prefix = safe_filename(prefix).casefold()
    pattern = re.compile(rf"^{re.escape(safe_prefix)}_(Pass|Fail)_[0-9]{{8}}_[0-9]{{6}}\.png$", re.IGNORECASE)
    return bool(pattern.match(path.name))
