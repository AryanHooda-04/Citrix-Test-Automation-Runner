# Citrix Test Automation Runner - Quick Start

## Install
1. Download the approved release ZIP from the internal SharePoint, OneDrive, or network location.
2. Extract the ZIP to:
   `C:\Users\<your-user>\Documents\CitrixTestAutomationRunner`
3. Open the extracted folder.
4. Double-click:
   `CitrixTestAutomationRunner.exe`

Do not run the app directly from inside the ZIP file.

## Run Testing
1. Launch your Citrix Desktop Viewer session.
2. Copy or type the exact Citrix Desktop Viewer title into **Citrix Desktop Name**.
   Example:
   `SILO01-TEST - Desktop Viewer`
3. Choose one option:
   - **Perform Complete Testing** for the full suite.
   - **Run All Mandatory Testcases** for mandatory evidence only.
   - **Run All Shakedown Testcases** for shakedown evidence only.
   - Individual **Run** buttons for a single testcase rerun.

## Evidence Location
Screenshots, logs, and Word reports are saved under your own Windows profile:

`C:\Users\<your-user>\Documents\CitrixTestAutomationRunner\evidence`

The app completion popup can open the report or screenshots folder directly.

## Rerunning A Failed Or Incorrect Evidence
If one screenshot is incorrect:
1. Run only that individual testcase again.
2. The previous screenshot for that testcase is replaced.
3. The latest Word report is refreshed when a previous Complete Testing report exists.

## Support Checklist
If the app does not start or automation does not work:
- Confirm the app was extracted locally, not run from the ZIP.
- Confirm Citrix Desktop Viewer is already open.
- Confirm the desktop name exactly matches the Citrix title.
- Confirm endpoint security has not quarantined the EXE.
- Send the latest log from:
  `Documents\CitrixTestAutomationRunner\evidence`
