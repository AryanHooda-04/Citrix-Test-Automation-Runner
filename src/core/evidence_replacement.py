from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable

from core.execution_log import safe_filename
from core.test_loader import TestCase


CAPTURE_EVIDENCE_PATTERN = re.compile(r"capture_evidence\(\s*[\"']([^\"']+)[\"']")
EVIDENCE_STATUS_PATTERN = re.compile(r"^(?P<prefix>.+)_(Pass|Fail)_[0-9]{8}_[0-9]{6}\.png$", re.IGNORECASE)
EXPLICIT_PREFIXES_BY_TEST_NAME = {
    "Office_Applications_Launch": (
        "word_evidence",
        "powerpnt_evidence",
        "excel_evidence",
    ),
    "IAT_Core_Application_Test_Evidence": (
        "7-zip_evidence",
        "adobe_acrobat_evidence",
        "Microsoft_Office_evidence",
        "Microsoft_Visio_evidence",
        "Microsoft_Project_evidence",
        "citrix_vda_evidence",
        "OpenJDK_JRE_evidence",
        "fslogix_apps_evidence",
    ),
}


def evidence_prefixes_for_test_case(test_case: TestCase) -> tuple[str, ...]:
    prefixes: set[str] = set()
    if test_case.capture_screenshot:
        prefixes.add(str(test_case.evidence_name or test_case.name))
    if test_case.evidence_name:
        prefixes.add(str(test_case.evidence_name))
    prefixes.update(EXPLICIT_PREFIXES_BY_TEST_NAME.get(test_case.name, ()))

    try:
        source = test_case.source_path.read_text(encoding="utf-8")
    except OSError:
        source = ""
    prefixes.update(match.group(1).strip() for match in CAPTURE_EVIDENCE_PATTERN.finditer(source))

    return tuple(sorted(prefix for prefix in prefixes if prefix))


def remove_existing_evidence_for_test_case(
    screenshots_dir: Path,
    test_case: TestCase,
    log_step: Callable[[str], None] | None = None,
) -> int:
    return remove_existing_evidence_for_prefixes(
        screenshots_dir,
        evidence_prefixes_for_test_case(test_case),
        log_step,
    )


def remove_existing_evidence_for_prefixes(
    screenshots_dir: Path,
    prefixes: Iterable[str],
    log_step: Callable[[str], None] | None = None,
) -> int:
    if not screenshots_dir.exists():
        return 0

    deleted_count = 0
    for prefix in sorted({safe_filename(value) for value in prefixes if value}):
        for path in screenshots_dir.glob(f"{prefix}_*.png"):
            if not _matches_evidence_prefix(path, prefix):
                continue
            try:
                path.unlink()
                deleted_count += 1
                if log_step is not None:
                    log_step(f"Removed previous evidence screenshot: {path}")
            except OSError as exc:
                if log_step is not None:
                    log_step(f"Unable to remove previous evidence screenshot {path}: {exc}")
    return deleted_count


def _matches_evidence_prefix(path: Path, safe_prefix: str) -> bool:
    match = EVIDENCE_STATUS_PATTERN.match(path.name)
    if not match:
        return False
    return match.group("prefix").casefold() == safe_prefix.casefold()
