# Citrix Test Automation Runner

Internal Windows desktop application for running Citrix evidence automation, validating screenshots, and generating Word evidence reports.

Current version: `2.0.0`

## What It Does

- Runs mandatory, shakedown, IAT, post-complete Zscaler, and Silo 43 specific evidence checks.
- Supports individual testcase runs, selected testcase runs, rerun failed, skip, pause, and stop controls.
- Captures desktop-scoped screenshots with Silo and Hostname overlay.
- Stores evidence under a desktop-specific folder so multiple silos do not overwrite each other.
- Generates and refreshes Word evidence reports from the latest valid screenshots.
- Provides evidence preview and failed-test recovery panels inside the app.
- Supports configurable evidence root location.
- Includes OCR validation and OpenAI Vision fallback validation for screenshots that need extra reliability.

## Main Components

```text
run_app.py                         App entry point
config/config.json                 Waits, paths, runtime mode, validation settings
src/gui/main_window.py             CustomTkinter UI and user workflow controls
src/core/runner.py                 Individual testcase execution and validation flow
src/core/master_runner.py          Mandatory, shakedown, complete, and scheduled execution
src/core/screenshot.py             Screenshot capture, overlay, and clipboard handling
src/core/word_report.py            Word report generation
src/core/ocr_validation.py         OCR validation helpers
src/core/windows_ocr.py            Windows OCR integration
src/core/ai_validation.py          OpenAI Vision fallback validation
src/core/openai_settings.py        Local OpenAI API key management
test_cases/                        UI automation test scripts
```

## Setup For Development

Use Python 3.13 on Windows.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_app.py
```

If PowerShell blocks activation scripts, launch a normal PowerShell as allowed by your endpoint policy or run Python directly from the virtual environment.

## Running The App

1. Open the required Citrix Desktop Viewer session.
2. Enter or select the exact Citrix Desktop Viewer title in **Citrix Desktop Name**.
3. Choose one of:
   - **Run All** under Perform Complete Testing
   - **Run All Mandatory Testcases**
   - **Run All Shakedown Testcases**
   - Individual testcase **Run**
   - **Run Selected**
4. Review progress, logs, and evidence from the right-side monitor panel.

## Evidence Output

Default evidence root:

```text
%USERPROFILE%\Documents\CitrixTestAutomationRunner\evidence
```

Each desktop gets its own folder:

```text
evidence\<Citrix Desktop Name>\
|-- logs\
|-- screenshots\
|   |-- Mandatory Evidence\
|   |-- Shakedown Evidence\
|   |-- IAT Evidence\
|   `-- Silo 43 Evidence\
`-- run_manifest.json
```

The app can also use a custom evidence root selected from the UI.

## Validation Flow

Screenshot validation is layered:

1. Capture screenshot.
2. Apply Silo and Hostname overlay.
3. Run OCR validation where available.
4. If OCR is inconclusive and AI fallback is enabled, call OpenAI Vision validation.
5. Retry only where the testcase flow supports retry.
6. Save final pass/fail evidence and update the run manifest.

The app uses Windows OCR packages for local OCR and OpenAI only as fallback when configured.

## OpenAI API Key

The OpenAI key is not stored in the repository or release package.

Lookup order:

1. `OPENAI_API_KEY` environment variable.
2. User-local saved key from the app.
3. Optional config value if explicitly added by an operator.

To update an expired key in the packaged app:

1. Launch the app.
2. Click **AI Key** in the header.
3. Paste the new key.
4. Click **Save Key**.

The key is saved locally under the Windows user profile:

```text
%APPDATA%\CitrixTestAutomationRunner\openai_settings.json
```

## Silo 43 Specific Testcases

Silo 43 specific checks are only intended for Silo 43 desktops:

- Oracle 12 bin path
- Nice Env Variables
- `C:\apps\vls`
- Ping `prod.dvfs.com`
- `C:\BAD` folder

The app blocks these testcases with a user-facing error popup when the selected desktop is not a Silo 43 desktop.

## Packaging

Build a release package with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1 -Version 2.0.0
```

Generated artifacts:

```text
release\Citrix_Test_Automation_Runner_v2.0.0\
release\Citrix_Test_Automation_Runner_v2.0.0.zip
```

The release includes the executable, config, test cases, quick-start documentation, and version file.

## Git Safety

Do not commit:

- `release/`
- `dist/`
- `build/`
- evidence screenshots or logs
- local desktop history
- OpenAI probe files
- API keys or secrets

The app stores user-level runtime secrets outside the repository.
