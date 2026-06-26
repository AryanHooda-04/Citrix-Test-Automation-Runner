from __future__ import annotations


MANDATORY_TEST_CASE_ORDER = [
    "Hostname_and_IP_Evidence",
    "Edge_WebView_Version_Evidence",
    "Edge_Browser_Version_Evidence",
    "Zscaler_Services_Evidence",
    "Google_and_Yahoo_Web_Access_Evidence",
    "Office_Applications_Launch",
    "Applist_Validation_Evidence",
]

APPLIST_TEST_CASE_NAME = "Applist_Validation_Evidence"
POST_COMPLETE_ZSCALER_TEST_NAME = "Zscaler_Services_Evidence_Post_Complete"

SHAKEDOWN_TEST_CASE_ORDER = [
    "Shakedown_Desktop_Availability_Evidence",
    "Shakedown_OneDrive_Sync_Evidence",
    "Shakedown_Edge_Sync_Evidence",
    "Shakedown_Edge_Policy_PAC_Evidence",
    "Shakedown_Windows_Version_Evidence",
    "Shakedown_Local_Network_Drives_Evidence",
    "Shakedown_FSLogix_Profile_Log_Evidence",
    "Shakedown_Temp_Folder_Evidence",
]

IAT_TEST_CASE_ORDER = [
    "IAT_Core_Application_Test_Evidence",
]

SILO43_TEST_CASE_ORDER = [
    "Silo43_Oracle_12_Bin_Path_Evidence",
    "Silo43_Nice_Env_Variables_Evidence",
    "Silo43_VLS_Privilege_Warning_Evidence",
    "Silo43_Ping_Prod_DVFS_Evidence",
    "Silo43_BAD_Folder_Evidence",
]

MANDATORY_EVIDENCE_FOLDER = "Mandatory Evidence"
SHAKEDOWN_EVIDENCE_FOLDER = "Shakedown Evidence"
IAT_EVIDENCE_FOLDER = "IAT Evidence"
SILO43_EVIDENCE_FOLDER = "Silo 43 Evidence"


def evidence_category_for_test_name(test_name: str) -> str | None:
    if test_name in MANDATORY_TEST_CASE_ORDER:
        return MANDATORY_EVIDENCE_FOLDER
    if test_name == POST_COMPLETE_ZSCALER_TEST_NAME:
        return MANDATORY_EVIDENCE_FOLDER
    if test_name in SHAKEDOWN_TEST_CASE_ORDER:
        return SHAKEDOWN_EVIDENCE_FOLDER
    if test_name in IAT_TEST_CASE_ORDER:
        return IAT_EVIDENCE_FOLDER
    if test_name in SILO43_TEST_CASE_ORDER:
        return SILO43_EVIDENCE_FOLDER
    if test_name.startswith("IAT_"):
        return IAT_EVIDENCE_FOLDER
    return None


def is_ring0_desktop(desktop_name: str | None) -> bool:
    return "ring0" in (desktop_name or "").casefold()


def is_silo43_desktop(desktop_name: str | None) -> bool:
    return "silo43" in (desktop_name or "").casefold()


def mandatory_order_for_desktop(desktop_name: str | None) -> list[str]:
    if is_ring0_desktop(desktop_name):
        return [name for name in MANDATORY_TEST_CASE_ORDER if name != APPLIST_TEST_CASE_NAME]
    return list(MANDATORY_TEST_CASE_ORDER)


def should_skip_test_for_desktop(test_name: str, desktop_name: str | None) -> bool:
    if test_name == APPLIST_TEST_CASE_NAME and is_ring0_desktop(desktop_name):
        return True
    if test_name in SILO43_TEST_CASE_ORDER and not is_silo43_desktop(desktop_name):
        return True
    return False


def skip_reason_for_test_and_desktop(test_name: str, desktop_name: str | None) -> str:
    if test_name == APPLIST_TEST_CASE_NAME and is_ring0_desktop(desktop_name):
        return "Skipped because Citrix Desktop Name contains Ring0; Applist validation is not required."
    if test_name in SILO43_TEST_CASE_ORDER and not is_silo43_desktop(desktop_name):
        return "Skipped because this testcase is only applicable to Silo 43 desktops."
    return "Skipped because this testcase is not applicable to the selected desktop."


def is_success_status(status: str) -> bool:
    return status in {"Pass", "Skipped"}
