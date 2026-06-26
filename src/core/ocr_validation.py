from __future__ import annotations

from collections import Counter
import re
from dataclasses import dataclass
from pathlib import Path

from core.windows_ocr import (
    WindowsOCRText,
    WindowsOCRUnavailable,
    extract_text_from_image,
    extract_text_from_image_region,
)


@dataclass(frozen=True)
class OCRValidationResult:
    valid: bool
    reason: str
    cmd_hostname: str = ""
    overlay_hostname: str = ""
    ipv4_addresses: tuple[str, ...] = ()
    version: str = ""
    session_id: str = ""
    license_id: str = ""
    raw_text: str = ""


OCRRegion = tuple[float, float, float, float]

REGION_BROWSER_WITHOUT_TASKBAR: tuple[OCRRegion, ...] = ((0.0, 0.0, 1.0, 0.93),)
REGION_CMD_CONSOLE_WITHOUT_OVERLAY: tuple[OCRRegion, ...] = ((0.0, 0.0, 0.88, 0.92),)
REGION_HOSTNAME_OVERLAY: tuple[OCRRegion, ...] = ((0.72, 0.72, 1.0, 0.96),)
REGION_POWERSHELL_CONSOLE: tuple[OCRRegion, ...] = ((0.0, 0.0, 0.97, 0.92),)
REGION_EDGE_SETTINGS_CONTENT: tuple[OCRRegion, ...] = ((0.24, 0.08, 0.98, 0.92),)
REGION_WINVER_DIALOG: tuple[OCRRegion, ...] = ((0.0, 0.0, 0.56, 0.80),)
REGION_PROGRAMS_AND_FEATURES: tuple[OCRRegion, ...] = ((0.10, 0.02, 0.99, 0.91),)
REGION_ZSCALER_WINDOW: tuple[OCRRegion, ...] = ((0.18, 0.04, 0.86, 0.88),)
REGION_FILE_EXPLORER_WITHOUT_TASKBAR: tuple[OCRRegion, ...] = ((0.0, 0.0, 1.0, 0.92),)
REGION_OFFICE_ABOUT_DIALOG: tuple[OCRRegion, ...] = ((0.16, 0.02, 0.86, 0.94),)
REGION_TEXT_EDITOR_WITHOUT_TASKBAR: tuple[OCRRegion, ...] = ((0.0, 0.0, 1.0, 0.92),)
REGION_POLICY_PAGE: tuple[OCRRegion, ...] = ((0.0, 0.0, 1.0, 0.93),)
REGION_POLICY_STATUS_COLUMN: tuple[OCRRegion, ...] = ((0.67, 0.12, 0.93, 0.92),)
REGION_SILO43_PATH_OUTPUT: tuple[OCRRegion, ...] = (
    (0.0, 0.11, 1.0, 0.155),
    (0.0, 0.10, 1.0, 0.18),
)

SILO43_ORACLE_12_BIN_PATH = r"C:\apps\oracle\12.1\client_32\bin"
SILO43_NICE_CODEC_PATH = r"C:\Program Files (x86)\Nice Systems\NICE Player Codec Pack"
SILO43_NICE_RELEASE_PATH = r"C:\Program Files (x86)\Nice Systems\NICE Player Release 6"
SILO43_VLS_PRIVILEGE_WARNING = "You do not have a VLS APPLICATION PRIVILIGE. Contact the Help Desk."


def validate_hostname_ip_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        console_ocr_text = _extract_text_for_ocr(image_path, REGION_CMD_CONSOLE_WITHOUT_OVERLAY)
        overlay_ocr_text = _extract_text_for_ocr(image_path, REGION_HOSTNAME_OVERLAY)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    console_lines = tuple(line.strip() for line in console_ocr_text.lines if line.strip())
    overlay_lines = tuple(line.strip() for line in overlay_ocr_text.lines if line.strip())
    console_text = "\n".join(console_lines) or console_ocr_text.text
    overlay_text = "\n".join(overlay_lines) or overlay_ocr_text.text
    full_text = "\n".join(part for part in (console_text, overlay_text) if part.strip())
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    cmd_hostname = _extract_hostname_command_output(console_lines, console_text)
    overlay_hostname = _extract_overlay_hostname(overlay_lines, overlay_text)
    if not overlay_hostname:
        combined_lines = tuple(line.strip() for line in full_text.splitlines() if line.strip())
        overlay_hostname = _extract_overlay_hostname(combined_lines, full_text)
    ipv4_addresses = _extract_ipv4_addresses(full_text)
    text_lower = full_text.casefold()

    if not cmd_hostname:
        cmd_hostname = _hostname_candidate_matching_overlay(console_text, overlay_hostname)
    if cmd_hostname and overlay_hostname and _hostname_key(cmd_hostname) != _hostname_key(overlay_hostname):
        matching_hostname = _hostname_candidate_matching_overlay(console_text, overlay_hostname)
        if matching_hostname:
            cmd_hostname = matching_hostname

    if not cmd_hostname:
        return OCRValidationResult(False, "OCR could not read hostname command output.", raw_text=full_text)
    if not overlay_hostname:
        return OCRValidationResult(
            False,
            "OCR could not read the bottom-right overlay hostname.",
            cmd_hostname=cmd_hostname,
            ipv4_addresses=ipv4_addresses,
            raw_text=full_text,
        )
    if _hostname_key(cmd_hostname) != _hostname_key(overlay_hostname):
        return OCRValidationResult(
            False,
            f"OCR hostname mismatch: command '{cmd_hostname}' vs overlay '{overlay_hostname}'.",
            cmd_hostname=cmd_hostname,
            overlay_hostname=overlay_hostname,
            ipv4_addresses=ipv4_addresses,
            raw_text=full_text,
        )
    if "ipconfig" not in text_lower and "windows ip configuration" not in text_lower and "ipv4" not in text_lower:
        return OCRValidationResult(
            False,
            "OCR could not confirm ipconfig output.",
            cmd_hostname=cmd_hostname,
            overlay_hostname=overlay_hostname,
            raw_text=full_text,
        )
    if not ipv4_addresses:
        return OCRValidationResult(
            False,
            "OCR could not read a valid IPv4 address.",
            cmd_hostname=cmd_hostname,
            overlay_hostname=overlay_hostname,
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR confirmed hostname, overlay hostname, and IPv4 output.",
        cmd_hostname=cmd_hostname,
        overlay_hostname=overlay_hostname,
        ipv4_addresses=ipv4_addresses,
        raw_text=full_text,
    )


def validate_silo43_oracle_12_bin_path_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    compact_text = re.sub(r"[^a-z0-9]+", "", full_text.casefold())
    if "cmdexe" not in compact_text and "microsoftwindows" not in compact_text and "commandprompt" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm Command Prompt is open.",
            raw_text=full_text,
        )

    if not _echo_path_command_seen(full_text):
        return OCRValidationResult(
            valid=False,
            reason='OCR could not confirm the "echo %path%" command was run.',
            raw_text=full_text,
        )

    path_text = _silo43_path_output_text(image_path, full_text)
    expected_entry = _normalize_windows_path_ocr(SILO43_ORACLE_12_BIN_PATH)
    for candidate in _oracle_path_output_candidates(path_text):
        normalized_candidate = _normalize_windows_path_ocr(candidate)
        if not normalized_candidate:
            continue
        if _candidate_starts_with_expected_oracle_path(normalized_candidate, expected_entry):
            return OCRValidationResult(
                valid=True,
                reason=(
                    "Windows OCR confirmed the first PATH entry is "
                    f"{SILO43_ORACLE_12_BIN_PATH}."
                ),
                raw_text=full_text,
            )

    first_entry = _first_detected_path_entry(path_text)
    if first_entry:
        reason = (
            "Oracle 12 bin path is not the first PATH entry. "
            f"Detected first entry: {first_entry}"
        )
    else:
        reason = (
            "OCR could not confirm the first PATH entry after echo %path%. "
            f"Expected {SILO43_ORACLE_12_BIN_PATH}."
        )
    return OCRValidationResult(valid=False, reason=reason, raw_text=full_text)


def validate_silo43_nice_env_variables_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    if not _echo_path_command_seen(full_text):
        return OCRValidationResult(
            valid=False,
            reason='OCR could not confirm the "echo %path%" command was run.',
            raw_text=full_text,
        )

    path_text = _silo43_path_output_text(image_path, full_text)
    entries = _path_entries_from_echo_output(path_text)
    if len(entries) < 3:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not read at least three PATH entries from the echo %path% output.",
            raw_text=full_text,
        )

    expected_second = _normalize_windows_path_ocr(SILO43_NICE_CODEC_PATH)
    expected_third = _normalize_windows_path_ocr(SILO43_NICE_RELEASE_PATH)
    if _path_entry_matches(entries[1], expected_second) and _path_entry_matches(entries[2], expected_third):
        return OCRValidationResult(
            valid=True,
            reason=(
                "Windows OCR confirmed the second and third PATH entries are "
                "NICE Player Codec Pack and NICE Player Release 6."
            ),
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=False,
        reason=(
            "NICE PATH entries are not in the required second and third positions. "
            f"Detected second='{entries[1][:140]}', third='{entries[2][:140]}'."
        ),
        raw_text=full_text,
    )


def validate_silo43_vls_privilege_warning_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)
    privilege_seen = any(
        token in compact_text
        for token in (
            "privilige",
            "privilege",
            "priviledge",
            "privllege",
            "priviiege",
        )
    )
    help_desk_seen = "helpdesk" in compact_text or ("help" in compact_text and "desk" in compact_text)
    missing_markers = []
    if "vls" not in compact_text:
        missing_markers.append("VLS")
    if "application" not in compact_text:
        missing_markers.append("APPLICATION")
    if not privilege_seen:
        missing_markers.append("PRIVILIGE")
    if "contact" not in compact_text:
        missing_markers.append("Contact")
    if not help_desk_seen:
        missing_markers.append("Help Desk")

    if missing_markers:
        return OCRValidationResult(
            valid=False,
            reason=(
                "OCR could not confirm the VLS privilege warning popup text. "
                f"Missing marker(s): {', '.join(missing_markers)}. "
                f"Expected text: {SILO43_VLS_PRIVILEGE_WARNING}"
            ),
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR confirmed the VLS APPLICATION PRIVILIGE Help Desk warning popup.",
        raw_text=full_text,
    )


def validate_silo43_ping_prod_dvfs_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)
    if "ping" not in compact_text or "proddvfscom" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason='OCR could not confirm the "ping prod.dvfs.com" command was run.',
            raw_text=full_text,
        )

    reply_visible = (
        "replyfrom" in compact_text
        or "bytes32" in compact_text
        or "bytes64" in compact_text
        or "ttl" in compact_text
    )
    if not reply_visible:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm a successful ping reply is visible.",
            raw_text=full_text,
        )

    zero_loss_visible = (
        "0loss" in compact_text
        or "lost0" in compact_text
        or bool(re.search(r"\b0\s*%\s*loss\b", text_lower))
        or bool(re.search(r"\blost\s*=\s*0\b", text_lower))
        or _ping_packet_counts_indicate_zero_loss(text_lower)
    )
    if not zero_loss_visible:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm 0% packet loss in the ping summary.",
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR confirmed ping prod.dvfs.com replies and 0% packet loss.",
        raw_text=full_text,
    )


def validate_silo43_bad_folder_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_FILE_EXPLORER_WITHOUT_TASKBAR)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)
    if "bad" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the C:\\BAD folder is open in File Explorer.",
            raw_text=full_text,
        )

    explorer_markers = (
        "fileexplorer" in compact_text or "explorer" in text_lower,
        "name" in text_lower,
        "new" in text_lower,
        "sort" in text_lower,
        "view" in text_lower,
        "date modified" in text_lower or "datemodified" in compact_text,
    )
    if sum(1 for marker in explorer_markers if marker) < 2:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm File Explorer is active with the Date modified column visible.",
            raw_text=full_text,
        )

    if "2025" in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR detected 2025 in the BAD folder modified date evidence; expected 2026.",
            raw_text=full_text,
        )
    if "2026" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm a 2026 modified date for the BAD application/file.",
            raw_text=full_text,
        )

    blocked_tokens = (
        "cmdexe",
        "commandprompt",
        "powershell",
        "microsoftedge",
        "google",
        "yahoo",
        "zscaler",
        "aboutmicrosoft",
    )
    for token in blocked_tokens:
        if token in compact_text:
            return OCRValidationResult(
                valid=False,
                reason=f"OCR detected another window instead of focused BAD File Explorer: {token}.",
                raw_text=full_text,
            )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR confirmed C:\\BAD is open and a 2026 modified date is visible.",
        raw_text=full_text,
    )


def validate_webview_version_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    return _validate_registry_version_with_windows_ocr(
        image_path,
        product_name="Microsoft Edge WebView2 Runtime",
        required_tokens=("webview2", "runtime"),
    )


def validate_edge_browser_version_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    return _validate_registry_version_with_windows_ocr(
        image_path,
        product_name="Microsoft Edge",
        required_tokens=("microsoft", "edge"),
        forbidden_tokens=("webview2",),
    )


def validate_edge_settings_version_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_EDGE_SETTINGS_CONTENT)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    if "settings" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm Edge Settings is open.",
            raw_text=full_text,
        )

    about_markers = (
        "about" in compact_text,
        "microsoftedgeforbusiness" in compact_text,
        "microsoftedgeisuptodate" in compact_text,
        "officialbuild" in compact_text,
    )
    if not any(about_markers):
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the Edge About page is visible in Settings.",
            raw_text=full_text,
        )

    if "webview2" in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR detected WebView2 content instead of the Edge browser About page.",
            raw_text=full_text,
        )

    version = _extract_version(full_text)
    if not version:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not read the Microsoft Edge version from the Settings About page.",
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason=f"Windows OCR confirmed Edge Settings About page with version {version}.",
        version=version,
        raw_text=full_text,
    )


def validate_windows_version_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_WINVER_DIALOG)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    if "aboutwindows" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason='OCR could not confirm the "About Windows" dialog is open.',
            raw_text=full_text,
        )

    if "windows11" not in compact_text and "windows10" not in compact_text and "microsoftwindows" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the Windows version dialog content.",
            raw_text=full_text,
        )

    version_match = re.search(r"\bversion\s*([0-9]{2}\s*[hH]\s*[12])\b", full_text, re.IGNORECASE)
    if version_match is None:
        version_match = re.search(r"version([0-9]{2}[hH][12])", compact_text, re.IGNORECASE)

    if version_match is None:
        return OCRValidationResult(
            valid=False,
            reason='OCR could not read a Windows release version like "22H2" or "24H2".',
            raw_text=full_text,
        )

    version = re.sub(r"\s+", "", version_match.group(1)).upper()
    return OCRValidationResult(
        valid=True,
        reason=f"Windows OCR confirmed About Windows with version {version}.",
        version=version,
        raw_text=full_text,
    )


def validate_7zip_programs_and_features_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_PROGRAMS_AND_FEATURES)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    programs_markers = (
        "programsandfeatures" in compact_text,
        "uninstallorchangeaprogram" in compact_text,
        "controlpanel" in compact_text,
    )
    if sum(1 for marker in programs_markers if marker) < 2:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the appwiz.cpl Programs and Features window is open.",
            raw_text=full_text,
        )

    seven_zip_markers = (
        "7-zip" in text_lower,
        "7 -zip" in text_lower,
        "7 zip" in text_lower,
        "7zip" in compact_text,
    )
    if not any(seven_zip_markers):
        return OCRValidationResult(
            valid=False,
            reason='OCR could not confirm Programs and Features is searching for "7-zip".',
            raw_text=full_text,
        )

    version = _extract_7zip_version(full_text)
    if version:
        return OCRValidationResult(
            valid=True,
            reason=f'Windows OCR confirmed Programs and Features is searching for 7-Zip and version {version} is listed.',
            version=version,
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason='Windows OCR confirmed Programs and Features is searching for 7-Zip; 7-Zip is not listed in the filtered results.',
        raw_text=full_text,
    )


def validate_adobe_acrobat_programs_and_features_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_PROGRAMS_AND_FEATURES)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    programs_markers = (
        "programsandfeatures" in compact_text,
        "uninstallorchangeaprogram" in compact_text,
        "controlpanel" in compact_text,
    )
    if sum(1 for marker in programs_markers if marker) < 2:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the appwiz.cpl Programs and Features window is open.",
            raw_text=full_text,
        )

    adobe_markers = (
        "adobe acrobat reader" in text_lower,
        "adobeacrobatreader" in compact_text,
    )
    if not any(adobe_markers):
        return OCRValidationResult(
            valid=False,
            reason='OCR could not confirm Programs and Features is searching for "Adobe Acrobat Reader".',
            raw_text=full_text,
        )

    version = _extract_adobe_acrobat_version(full_text)
    if version:
        return OCRValidationResult(
            valid=True,
            reason=(
                "Windows OCR confirmed Programs and Features is searching for Adobe Acrobat Reader "
                f"and version {version} is listed."
            ),
            version=version,
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason=(
            "Windows OCR confirmed Programs and Features is searching for Adobe Acrobat Reader; "
            "Adobe Acrobat Reader is not listed in the filtered results."
        ),
        raw_text=full_text,
    )


def validate_microsoft_office_programs_and_features_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    return _validate_programs_and_features_search_result(
        image_path=image_path,
        search_term="apps",
        displayed_name="Microsoft Office",
        listed_prefix_compacts=("microsoft365apps",),
    )


def validate_microsoft_visio_programs_and_features_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    return _validate_programs_and_features_search_result(
        image_path=image_path,
        search_term="visio",
        displayed_name="Microsoft Visio",
        listed_prefix_compacts=("microsoftvisio",),
    )


def validate_microsoft_project_programs_and_features_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    return _validate_programs_and_features_search_result(
        image_path=image_path,
        search_term="project",
        displayed_name="Microsoft Project",
        listed_prefix_compacts=("microsoftproject",),
    )


def validate_openjdk_jre_programs_and_features_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    return _validate_programs_and_features_search_result(
        image_path=image_path,
        search_term="JRE",
        displayed_name="OpenJDK / JRE",
        listed_prefix_compacts=("eclipsetemurinjre",),
    )


def validate_fslogix_apps_programs_and_features_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    return _validate_programs_and_features_search_result(
        image_path=image_path,
        search_term="fslogix",
        displayed_name="FSLogix",
        listed_prefix_compacts=("microsoftfslogixapps",),
    )


def validate_temp_folder_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_FILE_EXPLORER_WITHOUT_TASKBAR)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    if "temp" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the TEMP folder is open in File Explorer.",
            raw_text=full_text,
        )

    explorer_markers = (
        "new" in text_lower,
        "sort" in text_lower,
        "view" in text_lower,
        "details" in text_lower,
    )
    if sum(1 for marker in explorer_markers if marker) < 3:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm File Explorer is the active window for the TEMP folder screenshot.",
            raw_text=full_text,
        )

    blocked_tokens = (
        "searchresults",
        "noitemsmatchyoursearch",
        "notepad",
        "microsoftedge",
        "google",
        "yahoo",
        "settings",
        "aboutmicrosoft",
        "zscaler",
        "clientconnector",
    )
    for token in blocked_tokens:
        if token in compact_text:
            return OCRValidationResult(
                valid=False,
                reason=f"OCR detected another window instead of focused TEMP File Explorer: {token}.",
                raw_text=full_text,
            )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR confirmed the TEMP folder is open in focused File Explorer.",
        raw_text=full_text,
    )


def validate_zscaler_services_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    lines = tuple(line.strip() for line in ocr_text.lines if line.strip())
    full_text = "\n".join(lines) or ocr_text.text
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    if "zscaler" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the Zscaler Client Connector window.",
            raw_text=full_text,
        )

    if "clientconnector" not in compact_text and "connectivity" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the ZCCVDI connectivity screen.",
            raw_text=full_text,
        )

    if "servicestatus" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not read the Zscaler Service Status label.",
            raw_text=full_text,
        )

    if "authenticationstatus" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not read the Zscaler Authentication Status label.",
            raw_text=full_text,
        )

    if _has_standalone_status(full_text, "off"):
        return OCRValidationResult(
            valid=False,
            reason="Zscaler Service Status appears to be OFF.",
            raw_text=full_text,
        )

    if not _has_standalone_status(full_text, "on"):
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm Zscaler Service Status is ON.",
            raw_text=full_text,
        )

    if "notauthenticated" in compact_text or "unauthenticated" in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="Zscaler Authentication Status is not Authenticated.",
            raw_text=full_text,
        )

    if "authenticated" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm Zscaler Authentication Status is Authenticated.",
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR confirmed Zscaler Service Status ON and Authentication Status Authenticated.",
        raw_text=full_text,
    )


def validate_desktop_availability_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = extract_text_from_image(image_path)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    app_window_tokens = (
        "microsoftedge",
        "webview",
        "powershell",
        "cmdexe",
        "commandprompt",
        "settings",
        "fileexplorer",
        "searchresults",
        "notepad",
        "word",
        "excel",
        "powerpoint",
        "outlook",
        "onedrive",
        "zscaler",
        "clientconnector",
        "google",
        "yahoo",
        "aboutmicrosoft",
        "privacychoices",
        "restorepages",
        "sessionid",
        "licenseid",
        "plaintext",
        "closesearch",
        "searchoptions",
    )
    detected_tokens = tuple(token for token in app_window_tokens if token in compact_text)
    if detected_tokens:
        return OCRValidationResult(
            valid=False,
            reason=(
                "OCR detected another app window instead of a clean desktop view: "
                + ", ".join(detected_tokens[:5])
                + "."
            ),
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR did not detect any foreground application window text.",
        raw_text=full_text,
    )


def validate_onedrive_sync_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_FILE_EXPLORER_WITHOUT_TASKBAR)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    if "onedrive" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the OneDrive folder is open.",
            raw_text=full_text,
        )

    explorer_markers = (
        "new" in text_lower,
        "sort" in text_lower,
        "view" in text_lower,
        "details" in text_lower,
    )
    if sum(1 for marker in explorer_markers if marker) < 3:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm File Explorer is the active window.",
            raw_text=full_text,
        )

    blocked_tokens = (
        "searchresults",
        "noitemsmatchyoursearch",
        "notepad",
        "microsoftedge",
        "google",
        "yahoo",
        "settings",
        "powershell",
        "cmdexe",
        "aboutmicrosoft",
        "zscaler",
        "clientconnector",
    )
    for token in blocked_tokens:
        if token in compact_text:
            return OCRValidationResult(
                valid=False,
                reason=f"OCR detected another window instead of focused OneDrive File Explorer: {token}.",
                raw_text=full_text,
            )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR confirmed the OneDrive folder is open in focused File Explorer.",
        raw_text=full_text,
    )


def validate_local_network_drives_created_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    base_result = validate_onedrive_sync_with_windows_ocr(image_path)
    if not base_result.valid:
        return base_result

    text_lower = base_result.raw_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)
    if not _contains_local_evidence_folder_label(text_lower, compact_text):
        return OCRValidationResult(
            valid=False,
            reason='OCR could not confirm the newly created RunnerEvidence folder is visible before deletion.',
            raw_text=base_result.raw_text,
        )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR confirmed OneDrive File Explorer is open and the RunnerEvidence folder is visible before deletion.",
        raw_text=base_result.raw_text,
    )


def validate_local_network_drives_deleted_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    base_result = validate_onedrive_sync_with_windows_ocr(image_path)
    if not base_result.valid:
        return base_result

    text_lower = base_result.raw_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)
    if _contains_local_evidence_folder_label(text_lower, compact_text):
        return OCRValidationResult(
            valid=False,
            reason="OCR still shows the RunnerEvidence folder after the deletion screenshot was captured.",
            raw_text=base_result.raw_text,
        )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR confirmed OneDrive File Explorer is open and the RunnerEvidence folder is no longer visible after deletion.",
        raw_text=base_result.raw_text,
    )


def validate_edge_sync_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_EDGE_SETTINGS_CONTENT)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    if "settings" not in compact_text or "profiles" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm Edge Settings > Profiles is open.",
            raw_text=full_text,
        )

    sync_markers = ("synci son", "syncison", "synci5on", "sync1son", "sync ison")
    if not any(marker in compact_text or marker in text_lower for marker in sync_markers):
        return OCRValidationResult(
            valid=False,
            reason='OCR could not confirm the "Sync is on" status.',
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason='Windows OCR confirmed Edge sync status shows "Sync is on".',
        raw_text=full_text,
    )


def validate_policy_pac_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_POLICY_PAGE)
        status_ocr_text = _extract_text_for_ocr(image_path, REGION_POLICY_STATUS_COLUMN)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    status_text = _ocr_full_text(status_ocr_text)
    text_lower = full_text.casefold()
    status_lower = status_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)
    status_compact = re.sub(r"[^a-z0-9]+", "", status_lower)

    if "edge://policy" not in text_lower and "policies" not in text_lower and "policy" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the browser is open on edge://policy.",
            raw_text=full_text,
        )

    if "not ok" in status_lower or "notok" in status_compact:
        return OCRValidationResult(
            valid=False,
            reason='Policy PAC shows "NOT OK".',
            raw_text=f"{full_text}\n\n[Policy status column]\n{status_text}".strip(),
        )

    if "error" in status_lower:
        return OCRValidationResult(
            valid=False,
            reason='Policy PAC shows "Error".',
            raw_text=f"{full_text}\n\n[Policy status column]\n{status_text}".strip(),
        )

    return OCRValidationResult(
        valid=True,
        reason='Windows OCR confirmed edge://policy is visible and no "Error" or "NOT OK" status was detected.',
        raw_text=full_text,
    )


def validate_google_access_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_BROWSER_WITHOUT_TASKBAR)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)
    if _contains_web_error(text_lower, compact_text):
        return OCRValidationResult(
            valid=False,
            reason="OCR found a browser error page instead of Google.",
            raw_text=full_text,
        )

    has_google_url = "google.com" in text_lower or "wwwgooglecom" in compact_text
    google_markers = (
        "googlesearch" in compact_text
        or "imfeelinglucky" in compact_text
        or ("gmail" in compact_text and "images" in compact_text)
    )
    if not has_google_url:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the browser reached google.com.",
            raw_text=full_text,
        )
    if not google_markers:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the Google homepage loaded.",
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR confirmed google.com is accessible.",
        raw_text=full_text,
    )


def validate_yahoo_access_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_BROWSER_WITHOUT_TASKBAR)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)
    if _contains_web_error(text_lower, compact_text):
        return OCRValidationResult(
            valid=False,
            reason="OCR found a browser error page instead of Yahoo.",
            raw_text=full_text,
        )

    has_yahoo_url = "yahoo.com" in text_lower or "yahoocom" in compact_text
    yahoo_markers = (
        "yahoo" in compact_text
        and any(
            token in compact_text
            for token in (
                "mail",
                "nachrichten",
                "finanzen",
                "finance",
                "wetter",
                "anmelden",
                "news",
            )
        )
    )
    if not has_yahoo_url:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the browser reached yahoo.com.",
            raw_text=full_text,
        )
    if not yahoo_markers:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the Yahoo homepage loaded.",
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason="Windows OCR confirmed yahoo.com is accessible.",
        raw_text=full_text,
    )


def validate_office_about_with_windows_ocr(image_path: Path, product_name: str) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_OFFICE_ABOUT_DIALOG)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)
    product_token = re.sub(r"[^a-z0-9]+", "", product_name.casefold())

    if "aboutmicrosoft" not in compact_text and "about microsoft" not in text_lower:
        return OCRValidationResult(
            valid=False,
            reason=f"OCR could not confirm the About Microsoft {product_name} dialog is open.",
            raw_text=full_text,
        )

    if product_token not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason=f"OCR could not confirm the About dialog is for {product_name}.",
            raw_text=full_text,
        )

    has_office_version_context = (
        "microsoft365" in compact_text
        or "mso" in compact_text
        or ("version" in compact_text and "build" in compact_text)
    )
    if not has_office_version_context:
        return OCRValidationResult(
            valid=False,
            reason=f"OCR could not confirm {product_name} Microsoft 365 version details.",
            raw_text=full_text,
        )

    session_id = _extract_office_identifier(full_text, "session")
    if not session_id:
        return OCRValidationResult(
            valid=False,
            reason=f"OCR could not read the mandatory Session ID in the {product_name} About dialog.",
            raw_text=full_text,
        )

    license_id = _extract_office_identifier(full_text, "license")
    return OCRValidationResult(
        valid=True,
        reason=f"Windows OCR confirmed {product_name} About dialog with Session ID.",
        session_id=session_id,
        license_id=license_id,
        raw_text=full_text,
    )


def validate_applist_evidence_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_TEXT_EDITOR_WITHOUT_TASKBAR)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    explorer_result_tokens = (
        "searchresultsintemp",
        "noitemsmatchyoursearch",
        "closesearch",
        "searchoptions",
    )
    if any(token in compact_text for token in explorer_result_tokens):
        return OCRValidationResult(
            valid=False,
            reason="Applist evidence shows File Explorer search results instead of the opened Applist file.",
            raw_text=full_text,
        )

    has_applist_context = "applist" in compact_text
    has_text_editor_chrome = (
        ("file" in text_lower and "edit" in text_lower and "view" in text_lower)
        or ("ln" in compact_text and "col" in compact_text)
        or "plaintext" in compact_text
    )
    if not has_applist_context or not has_text_editor_chrome:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the Applist file is open in Notepad/Text Editor.",
            raw_text=full_text,
        )

    if "notok" not in compact_text and "not0k" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason='OCR could not confirm the "NOT OK" search text is visible.',
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason='Windows OCR confirmed the Applist file is open and "NOT OK" search is visible.',
        raw_text=full_text,
    )


def validate_fslogix_profile_log_with_windows_ocr(image_path: Path) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_TEXT_EDITOR_WITHOUT_TASKBAR)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    explorer_result_tokens = (
        "searchresults",
        "noitemsmatchyoursearch",
        "closesearch",
        "searchoptions",
    )
    if any(token in compact_text for token in explorer_result_tokens):
        return OCRValidationResult(
            valid=False,
            reason="FSLogix evidence shows File Explorer search results instead of the opened log file.",
            raw_text=full_text,
        )

    has_text_editor_chrome = (
        ("file" in text_lower and "edit" in text_lower and "view" in text_lower)
        or ("ln" in compact_text and "col" in compact_text)
        or "plaintext" in compact_text
    )
    if not has_text_editor_chrome:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the FSLogix log is open in Notepad/Text Editor.",
            raw_text=full_text,
        )

    if "copy" not in compact_text or "failure" not in compact_text:
        return OCRValidationResult(
            valid=False,
            reason='OCR could not confirm the "copy failure" search text is visible in the FSLogix log.',
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason='Windows OCR confirmed the FSLogix log is open and "copy failure" search is visible.',
        raw_text=full_text,
    )


def _contains_local_evidence_folder_label(text_lower: str, compact_text: str) -> bool:
    if "runnerevidence" in compact_text:
        return True

    text_markers = (
        "new folder",
        "new foider",
        "new fo1der",
        "new f0lder",
    )
    compact_markers = (
        "newfolder",
        "newfoider",
        "newfo1der",
        "newf0lder",
    )
    return any(marker in text_lower for marker in text_markers) or any(
        marker in compact_text for marker in compact_markers
    )


def _validate_registry_version_with_windows_ocr(
    image_path: Path,
    product_name: str,
    required_tokens: tuple[str, ...],
    forbidden_tokens: tuple[str, ...] = (),
) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_POWERSHELL_CONSOLE)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    lines = tuple(line.strip() for line in ocr_text.lines if line.strip())
    full_text = "\n".join(lines) or ocr_text.text
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    has_shell_context = any(
        token in text_lower
        for token in (
            "windows powershell",
            "microsoft windows",
            "cmd.exe",
            "ps c:",
            "powershell",
        )
    )
    if not has_shell_context:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm that Command Prompt or PowerShell is open.",
            raw_text=full_text,
        )

    command_visible = any(
        token in compact_text
        for token in (
            "getitemproperty",
            "getltemproperty",
        )
    )
    if not command_visible:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not read the Get-ItemProperty command in the screenshot.",
            raw_text=full_text,
        )

    error_tokens = (
        "cannot find path",
        "is not recognized",
        "access is denied",
        "access denied",
        "get-itemproperty :",
        "get-ltemproperty :",
        "exception",
        "error",
    )
    if any(token in text_lower for token in error_tokens):
        return OCRValidationResult(
            valid=False,
            reason="OCR found an error message in the PowerShell command output.",
            raw_text=full_text,
        )

    if any(token in compact_text for token in forbidden_tokens):
        return OCRValidationResult(
            valid=False,
            reason=f"OCR found output for a different product than {product_name}.",
            raw_text=full_text,
        )

    missing_tokens = [token for token in required_tokens if token not in compact_text]
    if missing_tokens:
        return OCRValidationResult(
            valid=False,
            reason=f"OCR could not confirm {product_name} output.",
            raw_text=full_text,
        )

    version = _extract_version(full_text)
    if not version:
        return OCRValidationResult(
            valid=False,
            reason=f"OCR could not read a {product_name} version value.",
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason=f"Windows OCR confirmed {product_name} version {version}.",
        version=version,
        raw_text=full_text,
    )


def _extract_hostname_command_output(lines: tuple[str, ...], full_text: str) -> str:
    console_lines = _lines_before_ipconfig(lines)
    console_text = _console_text_before_ipconfig(full_text)

    for index, line in enumerate(console_lines):
        if "hostname" not in line.casefold():
            continue
        inline_candidates = _hostname_candidates_from_text(line)
        inline = re.search(r"hostname\s+([A-Za-z0-9][A-Za-z0-9._-]{3,63})", line, re.IGNORECASE)
        if inline:
            inline_candidates.append(inline.group(1))
        hostname = _best_hostname_candidate(inline_candidates, allow_silo=False)
        if hostname:
            return hostname

        nearby_candidates: list[str] = []
        for candidate in console_lines[index + 1 : index + 5]:
            nearby_candidates.extend(_hostname_candidates_from_text(candidate))
            nearby_candidates.append(candidate)
        hostname = _best_hostname_candidate(nearby_candidates, allow_silo=False)
        if hostname:
            return hostname

    hostname = _extract_one_shot_hostname_output(console_lines, console_text)
    if hostname:
        return hostname

    match = re.search(
        r"hostname\s+([A-Za-z0-9][A-Za-z0-9._-]{3,63})",
        console_text,
        re.IGNORECASE,
    )
    hostname = _clean_hostname(match.group(1)) if match else ""
    if _looks_like_citrix_hostname(hostname) and not _looks_like_silo_desktop_name(hostname):
        return hostname
    return _best_hostname_candidate(_hostname_candidates_from_text(console_text), allow_silo=False)


def _extract_one_shot_hostname_output(lines: tuple[str, ...], full_text: str) -> str:
    """Handle cmd /k "hostname & ipconfig", where only the hostname output is visible."""

    candidates: list[str] = []
    for line in lines:
        lowered = line.casefold()
        if "windows ip configuration" in lowered or "ethernet adapter" in lowered:
            break
        candidates.extend(_hostname_candidates_from_text(line))

    console_prefix = _console_text_before_ipconfig(full_text)
    candidates.extend(_hostname_candidates_from_text(console_prefix))

    return _best_hostname_candidate(candidates, allow_silo=False)


def _lines_before_ipconfig(lines: tuple[str, ...]) -> tuple[str, ...]:
    before_ipconfig: list[str] = []
    for line in lines:
        lowered = line.casefold()
        if (
            "ipconfig" in lowered
            or "windows ip configuration" in lowered
            or "ethernet adapter" in lowered
            or "ipv4 address" in lowered
        ):
            break
        before_ipconfig.append(line)
    return tuple(before_ipconfig) or lines


def _console_text_before_ipconfig(text: str) -> str:
    match = re.search(
        r"\b(?:Windows\s+IP\s+Configuration|Ethernet\s+adapter|IPv4\s+Address)\b",
        text or "",
        re.IGNORECASE,
    )
    return (text or "")[: match.start()] if match else (text or "")


def _hostname_candidates_from_text(text: str) -> list[str]:
    normalized = _normalize_hostname_separators(text)
    patterns = (
        re.compile(
            r"V[A-Za-z0-9]{1,18}RW[A-Za-z0-9]{1,4}-[A-Za-z0-9][A-Za-z0-9._-]{1,31}",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b[A-Za-z][A-Za-z0-9]{1,30}-[A-Za-z0-9][A-Za-z0-9._-]{1,40}\b",
            re.IGNORECASE,
        ),
    )
    candidates: list[str] = []
    for pattern in patterns:
        candidates.extend(match.group(0) for match in pattern.finditer(normalized))
    return candidates


def _best_hostname_candidate(candidates: list[str], *, allow_silo: bool) -> str:
    unique: dict[str, str] = {}
    for candidate in candidates:
        hostname = _clean_hostname(candidate)
        if not hostname or not _looks_like_citrix_hostname(hostname):
            continue
        if not allow_silo and _looks_like_silo_desktop_name(hostname):
            continue
        unique.setdefault(hostname, hostname)
    if not unique:
        return ""
    return sorted(unique.values(), key=_hostname_candidate_score, reverse=True)[0]


def _hostname_candidate_score(hostname: str) -> int:
    candidate = _clean_hostname(hostname)
    score = 0
    if re.search(r"V[A-Z0-9]{1,18}RW[A-Z0-9]{1,4}-[A-Z0-9]", candidate):
        score += 20
    if "RW" in candidate and "-" in candidate:
        score += 10
    if candidate.startswith("V"):
        score += 5
    if "-" in candidate:
        score += 3
    if _looks_like_silo_desktop_name(candidate):
        score -= 50
    if "RING" in candidate or "TEST" in candidate:
        score -= 10
    return score


def _hostname_candidate_matching_overlay(text: str, overlay_hostname: str) -> str:
    if not overlay_hostname:
        return ""
    overlay_key = _hostname_key(overlay_hostname)
    for candidate in _hostname_candidates_from_text(text):
        hostname = _clean_hostname(candidate)
        if _hostname_key(hostname) == overlay_key:
            return hostname
    return ""


def _extract_overlay_hostname(lines: tuple[str, ...], full_text: str) -> str:
    for line in lines:
        match = re.search(r"Hostname\s*:\s*([A-Za-z0-9][A-Za-z0-9._-]{3,63})", line, re.IGNORECASE)
        if match:
            return _clean_hostname(match.group(1))

    match = re.search(r"Hostname\s*:\s*([A-Za-z0-9][A-Za-z0-9._-]{3,63})", full_text, re.IGNORECASE)
    return _clean_hostname(match.group(1)) if match else ""


def _extract_ipv4_addresses(text: str) -> tuple[str, ...]:
    addresses: list[str] = []
    pattern = re.compile(
        r"\b(\d{1,3})\s*\.\s*(\d{1,3})\s*\.\s*(\d{1,3})\s*\.\s*(\d{1,3})\b"
    )
    for match in pattern.finditer(text):
        octets = tuple(int(part) for part in match.groups())
        if any(part > 255 for part in octets):
            continue
        if octets == (0, 0, 0, 0) or octets[0] == 127:
            continue
        address = ".".join(str(part) for part in octets)
        if address not in addresses:
            addresses.append(address)
    return tuple(addresses)


def _extract_version(text: str) -> str:
    version_part = r"([0-9oOeE]{2,3}\s*\.\s*[0-9oOeE]+\s*\.\s*[0-9oOeE]+\s*\.\s*[0-9oOeE]+)"
    pv_match = re.search(
        rf"\bp\s*[vy]\b\s*[:=]?\s*{version_part}",
        text,
        re.IGNORECASE,
    )
    if pv_match:
        return _normalize_version_text(pv_match.group(1))

    lines = tuple(line.strip() for line in text.splitlines() if line.strip())
    for index, line in enumerate(lines):
        if not re.search(r"\bp\s*[vy]\b", line, re.IGNORECASE):
            continue
        inline_version = re.search(version_part, line, re.IGNORECASE)
        if inline_version:
            return _normalize_version_text(inline_version.group(1))
        for candidate in lines[index + 1 : index + 3]:
            next_line_version = re.search(version_part, candidate, re.IGNORECASE)
            if next_line_version:
                return _normalize_version_text(next_line_version.group(1))

    match = re.search(rf"\b{version_part}\b", text)
    return _normalize_version_text(match.group(1)) if match else ""


def _extract_7zip_version(text: str) -> str:
    match = re.search(
        r"7\s*[-–]?\s*zip\s*([0-9oOeE]+(?:\s*\.\s*[0-9oOeE]+){1,3})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return ""
    return _normalize_version_text(match.group(1))


def _extract_adobe_acrobat_version(text: str) -> str:
    match = re.search(
        r"adobe\s+acrobat\s+reader[^\n\r]*?([0-9oOeE]{2,3}(?:\s*\.\s*[0-9oOeE]+){2,4})",
        text,
        re.IGNORECASE,
    )
    if match:
        return _normalize_version_text(match.group(1))

    version_match = re.search(
        r"\bversion\b[^\n\r]*?([0-9oOeE]{2,3}(?:\s*\.\s*[0-9oOeE]+){2,4})",
        text,
        re.IGNORECASE,
    )
    if version_match:
        return _normalize_version_text(version_match.group(1))

    size_then_version = re.search(
        r"\b(?:gb|mb|kb)\b\s+([0-9oOeE]{2,3}(?:\s*\.\s*[0-9oOeE]+){2,4})",
        text,
        re.IGNORECASE,
    )
    if size_then_version:
        return _normalize_version_text(size_then_version.group(1))

    candidates = re.findall(
        r"\b([0-9oOeE]{2,3}(?:\s*\.\s*[0-9oOeE]+){2,4})\b",
        text,
        re.IGNORECASE,
    )
    for candidate in candidates:
        normalized = _normalize_version_text(candidate)
        parts = normalized.split(".")
        if len(parts) >= 3 and (len(parts[0]) >= 2 or any(len(part) >= 3 for part in parts[1:])):
            return normalized
    return ""


def _validate_programs_and_features_search_result(
    image_path: Path,
    search_term: str,
    displayed_name: str,
    listed_prefix_compacts: tuple[str, ...],
) -> OCRValidationResult:
    try:
        ocr_text = _extract_text_for_ocr(image_path, REGION_PROGRAMS_AND_FEATURES)
    except (OSError, RuntimeError, WindowsOCRUnavailable) as exc:
        return OCRValidationResult(valid=False, reason=f"Windows OCR failed: {exc}")

    full_text = _ocr_full_text(ocr_text)
    if not full_text.strip():
        return OCRValidationResult(valid=False, reason="Windows OCR returned no text.")

    text_lower = full_text.casefold()
    compact_text = re.sub(r"[^a-z0-9]+", "", text_lower)

    programs_markers = (
        "programsandfeatures" in compact_text,
        "uninstallorchangeaprogram" in compact_text,
        "controlpanel" in compact_text,
    )
    if sum(1 for marker in programs_markers if marker) < 2:
        return OCRValidationResult(
            valid=False,
            reason="OCR could not confirm the appwiz.cpl Programs and Features window is open.",
            raw_text=full_text,
        )

    search_compact = re.sub(r"[^a-z0-9]+", "", search_term.casefold())
    search_confirmed = f"search{search_compact}" in compact_text or f"qsearch{search_compact}" in compact_text
    programs_index = compact_text.find("programsandfeatures")
    if not search_confirmed and programs_index != -1 and search_compact in compact_text[:programs_index]:
        search_confirmed = True

    listed = any(prefix in compact_text for prefix in listed_prefix_compacts)
    if not search_confirmed and listed:
        search_confirmed = True

    if not search_confirmed:
        return OCRValidationResult(
            valid=False,
            reason=f'OCR could not confirm Programs and Features is searching for "{search_term}".',
            raw_text=full_text,
        )

    if not listed:
        return OCRValidationResult(
            valid=True,
            reason=(
                f'Windows OCR confirmed Programs and Features is searching for "{search_term}"; '
                f"{displayed_name} is not listed in the filtered results."
            ),
            raw_text=full_text,
        )

    version = _extract_most_common_programs_version(full_text)
    if version:
        return OCRValidationResult(
            valid=True,
            reason=(
                f'Windows OCR confirmed Programs and Features is searching for "{search_term}" '
                f"and {displayed_name} version {version} is listed."
            ),
            version=version,
            raw_text=full_text,
        )

    return OCRValidationResult(
        valid=True,
        reason=(
            f'Windows OCR confirmed Programs and Features is searching for "{search_term}" '
            f"and {displayed_name} is listed, but the version could not be read."
        ),
        raw_text=full_text,
    )


def _extract_most_common_programs_version(text: str) -> str:
    candidates = [
        _normalize_version_text(candidate)
        for candidate in re.findall(
            r"\b([0-9oOeE]{1,4}(?:\s*\.\s*[0-9oOeE]+){2,4})\b",
            text,
            re.IGNORECASE,
        )
    ]
    if not candidates:
        return ""
    return Counter(candidates).most_common(1)[0][0]


def _normalize_version_text(value: str) -> str:
    return (
        re.sub(r"\s+", "", value)
        .replace("O", "0")
        .replace("o", "0")
        .replace("E", "0")
        .replace("e", "0")
    )


def _extract_text_for_ocr(image_path: Path, regions: tuple[OCRRegion, ...] = ()) -> WindowsOCRText:
    if not regions:
        return extract_text_from_image(image_path)

    try:
        from PIL import Image
    except ImportError as exc:
        raise WindowsOCRUnavailable("Pillow is required for cropped OCR regions.") from exc

    image_path = image_path.resolve()
    with Image.open(image_path) as image:
        width, height = image.size

    parts: list[WindowsOCRText] = []
    for region in regions:
        ocr_part = extract_text_from_image_region(image_path, _scale_ocr_region(region, width, height))
        if _ocr_full_text(ocr_part).strip():
            parts.append(ocr_part)

    if not parts:
        return extract_text_from_image(image_path)
    return _combine_ocr_texts(parts)


def _scale_ocr_region(region: OCRRegion, width: int, height: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = region
    if max(abs(value) for value in region) <= 1.0:
        return (
            int(round(left * width)),
            int(round(top * height)),
            int(round(right * width)),
            int(round(bottom * height)),
        )
    return (int(round(left)), int(round(top)), int(round(right)), int(round(bottom)))


def _combine_ocr_texts(parts: list[WindowsOCRText]) -> WindowsOCRText:
    text_parts: list[str] = []
    lines: list[str] = []
    for part in parts:
        part_text = _ocr_full_text(part)
        if part_text:
            text_parts.append(part_text)
        lines.extend(line.strip() for line in part.lines if line.strip())

    return WindowsOCRText(text="\n".join(text_parts).strip(), lines=tuple(lines))


def _ocr_full_text(ocr_text: WindowsOCRText) -> str:
    lines = [_sanitize_ocr_text(line.strip()) for line in ocr_text.lines if line.strip()]
    raw_text = _sanitize_ocr_text(ocr_text.text or "")
    combined = "\n".join(lines)
    if raw_text and raw_text not in combined:
        combined = f"{combined}\n{raw_text}" if combined else raw_text
    return combined.strip()


def _sanitize_ocr_text(value: str) -> str:
    replacements = {
        "\u00a0": " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\uff1a": ":",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
    for source, target in replacements.items():
        value = value.replace(source, target)
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines())


def _oracle_path_output_candidates(text: str) -> tuple[str, ...]:
    lines = tuple(line.strip() for line in (text or "").splitlines() if line.strip())
    candidates: list[str] = []
    for line in lines:
        normalized_line = _normalize_windows_path_ocr(line)
        if _looks_like_path_output(normalized_line):
            candidates.append(normalized_line)
    for index, line in enumerate(lines):
        if not _echo_path_command_seen(line):
            continue
        if index + 1 < len(lines):
            candidates.append(" ".join(lines[index + 1 : index + 4]))
        candidates.append(" ".join(lines[index:]))

    normalized_full = _normalize_windows_path_ocr(text)
    for marker in ("echo%path%", "%path%"):
        marker_index = normalized_full.rfind(marker)
        if marker_index != -1:
            candidates.append(normalized_full[marker_index + len(marker) :])
            break
    else:
        if _looks_like_path_output(normalized_full):
            candidates.append(normalized_full)
    return tuple(candidate for candidate in candidates if candidate.strip())


def _normalize_windows_path_ocr(value: str) -> str:
    normalized = _sanitize_ocr_text(value or "").casefold()
    replacements = {
        "/": "\\",
        "|": "\\",
        "\u00a5": "\\",
        "\uff3c": "\\",
        "\u2216": "\\",
        "\u29f5": "\\",
        "’": "'",
        "`": "'",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    normalized = normalized.replace('"', "").replace("'", "")
    normalized = re.sub(r"\s+", "", normalized)
    normalized = normalized.replace("oracie", "oracle")
    normalized = normalized.replace("orac1e", "oracle")
    normalized = normalized.replace("client-32", "client_32")
    normalized = normalized.replace("client32", "client_32")
    normalized = re.sub(r"c[l1i]{1,3}ent[_\\-]?32", "client_32", normalized)
    normalized = re.sub(r"c[l1i]{1,3}ent\\32", r"client_32", normalized)
    normalized = normalized.replace("ciient_32", "client_32")
    normalized = normalized.replace("ciiient_32", "client_32")
    normalized = normalized.replace("c;\\", "c:\\")
    normalized = normalized.replace("c:\\\\", "c:\\")
    return normalized


def _echo_path_command_seen(text: str) -> bool:
    compact_text = re.sub(r"[^a-z0-9%]+", "", (text or "").casefold())
    return "echo%path%" in compact_text or "%path%" in compact_text or "echopath" in compact_text


def _candidate_starts_with_expected_oracle_path(candidate: str, expected_entry: str) -> bool:
    tail = candidate
    for marker in ("echo%path%", "%path%"):
        marker_index = tail.rfind(marker)
        if marker_index != -1:
            tail = tail[marker_index + len(marker) :]
            break
    tail = tail.lstrip(">:;")

    if tail.startswith(expected_entry):
        return True
    semicolon_index = tail.find(";")
    first_entry = tail[:semicolon_index] if semicolon_index != -1 else tail[: len(expected_entry) + 20]
    return first_entry.startswith(expected_entry) or expected_entry in first_entry[: len(expected_entry) + 8]


def _path_entries_from_echo_output(text: str) -> list[str]:
    best_entries: list[str] = []
    expected_first = _normalize_windows_path_ocr(SILO43_ORACLE_12_BIN_PATH)
    for candidate in _oracle_path_output_candidates(text):
        normalized = _normalize_windows_path_ocr(candidate)
        for marker in ("echo%path%", "%path%"):
            marker_index = normalized.rfind(marker)
            if marker_index != -1:
                normalized = normalized[marker_index + len(marker) :]
                break
        normalized = normalized.lstrip(">:;")
        entries = [entry.strip() for entry in normalized.split(";") if entry.strip()]
        if len(entries) >= 3 and _candidate_starts_with_expected_oracle_path(entries[0], expected_first):
            return entries
        if len(entries) > len(best_entries):
            best_entries = entries
        if len(best_entries) >= 3:
            break
    return best_entries


def _path_entry_matches(actual_entry: str, expected_entry: str) -> bool:
    actual = actual_entry.rstrip("\\")
    expected = expected_entry.rstrip("\\")
    return actual == expected


def _silo43_path_output_text(image_path: Path, full_text: str) -> str:
    parts: list[str] = []
    try:
        crop_text = _ocr_full_text(_extract_text_for_ocr(image_path, REGION_SILO43_PATH_OUTPUT))
    except (OSError, RuntimeError, WindowsOCRUnavailable):
        crop_text = ""
    if crop_text.strip():
        parts.append(crop_text)
    if full_text.strip():
        parts.append(full_text)
    return "\n".join(dict.fromkeys(parts))


def _looks_like_path_output(normalized_text: str) -> bool:
    return "c:\\" in normalized_text and ";" in normalized_text


def _first_detected_path_entry(text: str) -> str:
    for candidate in _oracle_path_output_candidates(text):
        normalized = _normalize_windows_path_ocr(candidate)
        for marker in ("echo%path%", "%path%"):
            marker_index = normalized.rfind(marker)
            if marker_index != -1:
                normalized = normalized[marker_index + len(marker) :]
                break
        normalized = normalized.lstrip(">:;")
        if not normalized:
            continue
        semicolon_index = normalized.find(";")
        first_entry = normalized[:semicolon_index] if semicolon_index != -1 else normalized
        if first_entry:
            return first_entry[:140]
    return ""


def _ping_packet_counts_indicate_zero_loss(text_lower: str) -> bool:
    sent = _ocr_ping_count(text_lower, "sent")
    received = _ocr_ping_count(text_lower, "received")
    lost = _ocr_ping_count(text_lower, "lost", allow_zero_ocr_variants=True)
    if sent is None or received is None:
        return False
    if sent <= 0 or sent != received:
        return False
    return lost in (None, 0)


def _ocr_ping_count(text_lower: str, label: str, allow_zero_ocr_variants: bool = False) -> int | None:
    pattern = rf"\b{re.escape(label)}\s*=\s*([0-9@oO])"
    match = re.search(pattern, text_lower)
    if not match:
        return None
    value = match.group(1)
    if allow_zero_ocr_variants and value in {"@", "o", "O"}:
        return 0
    if value.isdigit():
        return int(value)
    return None


def _contains_web_error(text_lower: str, compact_text: str) -> bool:
    error_tokens = (
        "can't reach this page",
        "cant reach this page",
        "this site can't be reached",
        "this site cant be reached",
        "cannot be reached",
        "no internet",
        "dns_probe",
        "err_",
        "hmmm",
    )
    return any(token in text_lower or token.replace(" ", "") in compact_text for token in error_tokens)


def _extract_office_identifier(text: str, label: str) -> str:
    label_pattern = re.escape(label)
    for line in text.splitlines():
        if not re.search(label_pattern, line, re.IGNORECASE):
            continue
        match = re.search(
            rf"\b{label_pattern}\s*(?:i?d|1d|d)?\s*[:;]?\s*([A-Za-z0-9][A-Za-z0-9._\-\s]{{7,140}})",
            line,
            re.IGNORECASE,
        )
        if not match:
            continue
        identifier = _clean_office_identifier(match.group(1))
        if identifier:
            return identifier
    return ""


def _clean_office_identifier(value: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "", (value or "").strip())
    candidate = candidate.strip("._-")
    if len(candidate) < 8:
        return ""
    if not any(char.isalpha() for char in candidate) or not any(char.isdigit() for char in candidate):
        return ""
    return candidate


def _has_standalone_status(text: str, value: str) -> bool:
    if value.casefold() == "on":
        pattern = r"\b[o0]n\b"
    else:
        pattern = rf"\b{re.escape(value)}\b"
    return re.search(pattern, text, re.IGNORECASE) is not None


_OCR_HOSTNAME_DIGIT_TRANSLATION = str.maketrans(
    {
        "O": "0",
        "Q": "0",
        "D": "0",
        "E": "0",
        "I": "1",
        "L": "1",
        "S": "5",
        "B": "8",
        "G": "6",
    }
)


def _clean_hostname(value: str) -> str:
    candidate = _normalize_hostname_separators(value)
    candidate = re.sub(r"[^A-Za-z0-9-]", "", candidate.strip()).upper()
    if not candidate:
        return ""
    candidate = re.sub(r"^YFLA(\d+)RW", r"VTA\1RW", candidate)
    candidate = re.sub(r"^([A-Z]{3})I(?=RW[A-Z0-9]{2}-)", r"\g<1>1", candidate)
    return _repair_hostname_ocr_digits(candidate)


def _repair_hostname_ocr_digits(candidate: str) -> str:
    match = re.fullmatch(
        r"(?P<prefix>[A-Z0-9]{2,12}RW)(?P<silo>[A-Z0-9]{2})-(?P<body>[A-Z0-9-]{2,40})",
        candidate,
    )
    if not match:
        return candidate

    silo = _normalize_ocr_digit_run(match.group("silo"))
    body = _repair_hostname_body_digits(match.group("body"))
    return f"{match.group('prefix')}{silo}-{body}"


def _repair_hostname_body_digits(body: str) -> str:
    match = re.fullmatch(r"(?P<site>[A-Z0-9]{2})(?P<letter>[A-Z])(?P<suffix>[A-Z0-9]{2,})", body)
    if not match:
        return body

    site = _normalize_ocr_digit_run(match.group("site"))
    suffix = _normalize_ocr_digit_run(match.group("suffix"))
    return f"{site}{match.group('letter')}{suffix}"


def _normalize_ocr_digit_run(value: str) -> str:
    return value.translate(_OCR_HOSTNAME_DIGIT_TRANSLATION)


def _normalize_hostname_separators(value: str) -> str:
    return (
        (value or "")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .replace("€", "0")
    )


def _hostname_key(value: str) -> str:
    return _clean_hostname(value).replace("I", "1")


def _looks_like_citrix_hostname(value: str) -> bool:
    candidate = _clean_hostname(value)
    if not re.fullmatch(r"[A-Z0-9](?:[A-Z0-9-]{1,61}[A-Z0-9])?", candidate):
        return False
    if len(candidate) < 3 or len(candidate) > 63:
        return False
    if candidate.isdigit():
        return False
    if not any(char.isdigit() for char in candidate) and "-" not in candidate:
        return False
    if "-" not in candidate and not re.search(r"RW\d{2}", candidate):
        return False
    if not (
        re.search(r"RW\d{2}-[A-Z0-9]", candidate)
        or candidate.startswith(("VTA", "VTE", "VTN", "VPD", "SILO"))
    ):
        return False
    return candidate not in {"HOSTNAME", "IPCONFIG", "WINDOWS", "CONFIGURATION", "UNKNOWN"}


def _looks_like_silo_desktop_name(value: str) -> bool:
    candidate = _clean_hostname(value)
    return candidate.startswith("SILO")
