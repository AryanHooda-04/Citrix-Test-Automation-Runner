param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $Version) {
    $Version = (Get-Content -Path "version.txt" -Raw).Trim()
}
if (-not $Version) {
    throw "Version is empty. Update version.txt or pass -Version."
}

$ReleaseRoot = Join-Path $Root "release"
$DistApp = Join-Path $Root "dist\CitrixTestAutomationRunner"
$ReleaseName = "Citrix_Test_Automation_Runner_v$Version"
$ReleaseDir = Join-Path $ReleaseRoot $ReleaseName
$ZipPath = Join-Path $ReleaseRoot "$ReleaseName.zip"

if (Test-Path $ReleaseDir) {
    Remove-Item -LiteralPath $ReleaseDir -Recurse -Force
}
if (Test-Path $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

python -m PyInstaller --noconfirm --clean "CitrixTestAutomationRunner.spec"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE."
}

New-Item -ItemType Directory -Path $ReleaseDir | Out-Null
Copy-Item -Path (Join-Path $DistApp "*") -Destination $ReleaseDir -Recurse -Force
Copy-Item -LiteralPath "config" -Destination (Join-Path $ReleaseDir "config") -Recurse -Force
Copy-Item -LiteralPath "test_cases" -Destination (Join-Path $ReleaseDir "test_cases") -Recurse -Force
Copy-Item -LiteralPath "README_Quick_Start.md" -Destination $ReleaseDir -Force
Copy-Item -LiteralPath "README_Team_Rollout.md" -Destination $ReleaseDir -Force
Copy-Item -LiteralPath "version.txt" -Destination $ReleaseDir -Force

$HistoryFile = Join-Path $ReleaseDir "config\desktop_history.json"
if (Test-Path $HistoryFile) {
    Remove-Item -LiteralPath $HistoryFile -Force
}

Compress-Archive -Path (Join-Path $ReleaseDir "*") -DestinationPath $ZipPath -Force

Write-Host ""
Write-Host "Release folder: $ReleaseDir"
Write-Host "Release ZIP:    $ZipPath"
Write-Host ""
Write-Host "Pilot install path recommendation:"
Write-Host "  %USERPROFILE%\Documents\CitrixTestAutomationRunner"
