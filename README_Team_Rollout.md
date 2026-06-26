# Citrix Test Automation Runner - Team Rollout

![Version](https://img.shields.io/badge/version-2.0.0-1173C4)
![Packaging](https://img.shields.io/badge/package-PyInstaller%20ZIP-475569)
![Deployment](https://img.shields.io/badge/deployment-portable%20Windows%20app-14B8A6)

This document is for maintainers, testers, and team leads distributing Citrix Test Automation Runner to multiple users.

## Recommended Distribution Model

Distribute the runner as a versioned portable Windows ZIP:

```text
Citrix_Test_Automation_Runner_v2.0.0.zip
```

Each tester should extract the ZIP locally:

```text
%USERPROFILE%\Documents\CitrixTestAutomationRunner
```

The packaged app does not require Python on tester machines because the runtime is bundled by PyInstaller.

## What The Release Contains

```text
CitrixTestAutomationRunner.exe
_internal\
config\config.json
test_cases\
README.md
README_Quick_Start.md
README_Team_Rollout.md
version.txt
```

The release intentionally excludes:

- local evidence
- old logs
- build folders
- virtual environments
- local desktop history
- OpenAI API keys
- user-specific runtime settings

## Build A Release

From the project root on the build machine:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1 -Version 2.0.0
```

Expected output:

```text
release\Citrix_Test_Automation_Runner_v2.0.0\
release\Citrix_Test_Automation_Runner_v2.0.0.zip
```

## Tester Data Location

By default, runtime data is written under the tester's Windows profile:

```text
%USERPROFILE%\Documents\CitrixTestAutomationRunner\evidence
```

This keeps evidence separated from the shared release package and prevents multiple testers from overwriting each other.

The app also supports a custom Evidence Root selected from the UI.

## Rollout Checklist

Before wider distribution:

| Check | Owner | Expected Result |
| --- | --- | --- |
| Security review | IT / security | EXE is allowed or approved for pilot |
| Pilot install | Maintainer | ZIP extracts and EXE launches without Python |
| Citrix activation | Tester | App can target the intended Desktop Viewer title |
| Screenshot capture | Tester | Screenshots save under desktop-specific folders |
| Clipboard copy | Tester | Screenshot clipboard behavior works where required |
| Word report | Tester | Report generation completes and can be downloaded |
| Recovery flow | Tester | Failed testcase can be rerun without rerunning the full suite |
| AI Key flow | Maintainer | Masked key entry, Test Key, Save Key, and Clear Saved work |

## Update Strategy

| Change Type | Recommended Update |
| --- | --- |
| Wait-time or coordinate adjustment | Replace `config\config.json` if no code changed |
| Testcase script update | Replace `test_cases\` or ship a patch ZIP |
| UI/core logic update | Ship a new versioned ZIP |
| Validation/reporting update | Ship a new versioned ZIP |
| Major feature update | Pilot first, then distribute |

Keep prior evidence folders untouched during upgrades.

## AI Key Guidance

Do not hardcode or distribute OpenAI API keys in the repository or release ZIP.

Supported key sources:

1. `OPENAI_API_KEY` environment variable.
2. User-local saved key configured through **AI Key** in the app.
3. Optional config value only if explicitly approved.

Recommended team approach:

- A maintainer configures the key on each approved machine through the app UI.
- The app masks the key and stores it only under the current Windows user profile.
- If the key expires, update it through **AI Key > Clear Saved > Save Key**.

Local key path:

```text
%APPDATA%\CitrixTestAutomationRunner\openai_settings.json
```

## Scheduled Desktop Testing Note

The app supports scheduled Complete Testing for multiple desktops, but Citrix sessions can log out while waiting. Use scheduled testing only when all target desktops remain authenticated and active for the full batch duration.

If idle sessions require QR or password login, run desktops one at a time.

## Security Validation

Before sending the ZIP broadly:

1. Share the release with IT/security for approval or hash whitelisting.
2. Pilot with two testers.
3. Confirm endpoint security does not quarantine the app.
4. Confirm the app runs without requiring Python.
5. Confirm output stays under the tester profile or selected Evidence Root.
6. Confirm no API key or local evidence is included in the ZIP.

If unsigned EXEs are blocked, ask IT to deploy the same release folder through the approved software distribution channel.

## Support And Escalation

When a tester reports an issue, request:

- App version.
- Desktop name entered.
- Exact testcase or suite.
- Screenshot path.
- JSON log path.
- Word report path if generated.
- Whether the issue happened during OCR, AI fallback, screenshot capture, Citrix focus, or report generation.

Useful paths:

```text
%USERPROFILE%\Documents\CitrixTestAutomationRunner\evidence
%APPDATA%\CitrixTestAutomationRunner
```

## Maintainer Git Safety

Do not commit:

- `release/`
- `dist/`
- `build/`
- `.venv/`
- evidence folders
- local logs
- API keys
- user-specific runtime settings

Release packages and evidence output should remain outside source control.
