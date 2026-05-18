@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" run_app.py
) else (
  python run_app.py
)

if errorlevel 1 (
  echo.
  echo Application exited with an error.
  echo Install dependencies with: pip install -r requirements.txt
  pause
)
