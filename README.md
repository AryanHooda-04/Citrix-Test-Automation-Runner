# Citrix Test Automation Runner

Windows desktop application for manual testers who run UI-only automation on Citrix desktops and need screenshots copied straight into Word evidence documents.

## Architecture

The application is split into small responsibilities:

- `run_app.py` starts the Tkinter Windows GUI.
- `src/gui/main_window.py` renders test case buttons, status indicators, and live execution messages.
- `src/core/test_loader.py` discovers test scripts dynamically from `test_cases/`.
- `src/core/runner.py` executes one selected test case, records pass/fail, and triggers evidence capture.
- `src/core/automation_context.py` exposes safe keyboard/mouse helper methods around PyAutoGUI and configurable waits.
- `src/core/screenshot.py` captures screenshots and copies them to the Windows clipboard as an image.
- `src/core/execution_log.py` writes structured JSON logs per execution.
- `src/core/config.py` loads wait times and local output paths from `config/config.json`.

Automation is intentionally keyboard/mouse based so it can work against Citrix sessions where object-level automation is unavailable.

## Folder Structure

```text
.
|-- config/
|   `-- config.json
|-- evidence/
|   `-- Citrix_Desktop_Name/
|       |-- logs/
|       `-- screenshots/
|-- src/
|   |-- core/
|   |   |-- automation_context.py
|   |   |-- config.py
|   |   |-- execution_log.py
|   |   |-- runner.py
|   |   |-- screenshot.py
|   |   `-- test_loader.py
|   `-- gui/
|       `-- main_window.py
|-- test_cases/
|   `-- sample_citrix_login.py
|-- requirements.txt
`-- run_app.py
```

## Setup

Use Python 3.10 or newer on Windows.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_app.py
```

## Configuration

Wait times are controlled in `config/config.json`.

```json
{
  "waits": {
    "default_action_wait_sec": 0.5,
    "after_click_wait_sec": 1.0,
    "after_type_wait_sec": 0.4,
    "after_hotkey_wait_sec": 1.0,
    "citrix_screen_settle_sec": 2.0,
    "screenshot_settle_sec": 0.8
  }
}
```

Test scripts should call the helper waits from `ctx` instead of using hardcoded `time.sleep`.

## Adding A Test Case

Create a new Python file in `test_cases/` and expose a `TEST_CASE` dictionary plus a `run(ctx)` function.

```python
TEST_CASE = {
    "id": "TC_002",
    "name": "Open Customer Search",
    "description": "Opens the customer search screen from the Citrix application."
}

def run(ctx):
    ctx.step("Bring Citrix session into focus")
    ctx.hotkey("alt", "tab")

    ctx.step("Open customer search")
    ctx.hotkey("ctrl", "f")
    ctx.type_text("Smith")
    ctx.press("enter")

    ctx.wait_for_citrix()
```

After saving the file, restart the app or click `Refresh`. The test appears as a new button automatically.

## Included Hostname Test

`test_cases/hostname_validation.py` adds the `Hostname_Validation` button. It looks for the local Citrix Desktop Viewer window containing `VPDW-LQ7W7LR - Desktop Viewer`, activates it, clicks the center of the Citrix session, opens the Run dialog with `Windows + R`, starts Command Prompt, runs `hostname`, verifies copied CMD text contains output after the command, and then lets the runner capture:

```text
evidence/Citrix_Desktop_Name/screenshots/Hostname_Validation_Pass_YYYYMMDD_HHMMSS.png
```

If the Citrix Viewer window is not found, the test is marked `Fail`, a failure log is written, and a failure screenshot is captured when configured.

`test_cases/hostname_and_ip_evidence.py` adds the `Hostname_and_IP_Evidence` button. The tester enters the Citrix desktop title in the `Citrix Desktop Name` field before running. The test activates a Citrix Viewer window whose title contains that value, opens Command Prompt through `Windows + R`, runs `hostname` without validation, runs `ipconfig`, waits for the IP information to render, and then captures:

```text
evidence/Citrix_Desktop_Name/screenshots/Hostname_and_IP_Evidence_Pass_YYYYMMDD_HHMMSS.png
```

`test_cases/edge_webview_version_evidence.py` adds the `Edge_WebView_Version_Evidence` button. It uses the entered `Citrix Desktop Name` to activate a Citrix Viewer window whose title contains that value, opens Command Prompt, starts PowerShell inside it, runs the Edge WebView registry command, verifies output is visible, and captures:

```text
evidence/Citrix_Desktop_Name/screenshots/webview_evidence_Pass_YYYYMMDD_HHMMSS.png
```

`test_cases/edge_browser_version_evidence.py` adds the `Edge_Browser_Version_Evidence` button. It follows the same Citrix and PowerShell flow, runs the Microsoft Edge browser registry command, waits for output to render, and captures:

```text
evidence/Citrix_Desktop_Name/screenshots/edge_evidence_Pass_YYYYMMDD_HHMMSS.png
```

`test_cases/zscaler_services_evidence.py` adds the `Zscaler_Services_Evidence` button. It activates the entered Citrix desktop, opens Windows Search, launches `ZCCVDI`, waits for the application UI to load, and captures:

```text
evidence/Citrix_Desktop_Name/screenshots/zscaler_evidence_Pass_YYYYMMDD_HHMMSS.png
```

`test_cases/office_applications_launch.py` adds the `Office_Applications_Launch` button. It activates the entered Citrix desktop, then handles each app independently: launch Word, maximize, capture Account > About evidence, close Word; repeat for PowerPoint and Excel.

`test_cases/google_and_yahoo_web_access_evidence.py` adds the `Google_and_Yahoo_Web_Access_Evidence` button. It activates the entered Citrix desktop, opens `google.com` and `yahoo.com` through Windows Search/Edge, handles consent prompts through Tab/Enter keyboard navigation, and captures `google_evidence` and `yahoo_evidence` screenshots.

`test_cases/applist_validation_evidence.py` adds the `Applist_Validation_Evidence` button. It opens `C:\Temp` in File Explorer, searches for `Applist`, clicks the configured first-result coordinate in the maximized Explorer window, opens the result, searches inside the file for `NOT OK`, and captures `applist_evidence`.

## GUI Button-To-Script Mapping

The GUI calls `discover_test_cases()` in `src/core/test_loader.py`. Each valid script in `test_cases/` becomes one button using:

- Button text: `TEST_CASE["name"]`
- Execution target: the script module's `run(ctx)` function
- Status row: `Idle`, `Running`, `Pass`, or `Fail`

## Screenshot And Clipboard Evidence

Evidence files are grouped under a desktop-specific folder derived from the `Citrix Desktop Name` entered before the run. For example, `SILO27-TEST - Desktop Viewer` is stored under `evidence/SILO27-TEST___Desktop_Viewer/`.

On pass, the runner captures the full screen after the Citrix settle wait and saves:

```text
evidence/Citrix_Desktop_Name/screenshots/TestCaseName_Pass_YYYYMMDD_HHMMSS.png
```

It also copies the image to the Windows clipboard, so the tester can open Word and press `Ctrl+V`.

On failure, the runner writes error details to the log and, when enabled, captures:

```text
evidence/Citrix_Desktop_Name/screenshots/TestCaseName_Fail_YYYYMMDD_HHMMSS.png
```

Pass and fail evidence files use different names and are never overwritten.

## Logging

Each execution writes one JSON file in `evidence/Citrix_Desktop_Name/logs/`.

Example:

```json
{
  "test_case": "Sample Citrix Login",
  "status": "Pass",
  "start_time": "2026-04-30T14:38:11",
  "end_time": "2026-04-30T14:38:24",
  "duration_seconds": 13.2,
  "steps": [
    {
      "timestamp": "2026-04-30T14:38:12",
      "level": "INFO",
      "message": "Bring Citrix session into focus"
    }
  ],
  "error": null,
  "screenshot": "evidence/Citrix_Desktop_Name/screenshots/Sample_Citrix_Login_Pass_20260430_143824.png"
}
```

## Citrix Reliability Notes

- Keep scripts action-oriented and close to manual steps.
- Prefer keyboard shortcuts over coordinates where possible.
- Use `ctx.wait_for_citrix()` after screen transitions.
- Use coordinates only when the Citrix app has no reliable keyboard path.
- Increase waits in `config/config.json` for slower Citrix sessions.
- Move the mouse to safe positions before clicks if popups or overlays are common.
