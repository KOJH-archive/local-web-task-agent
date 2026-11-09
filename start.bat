@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [Web Task Agent] First-time setup is starting...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
  if errorlevel 1 (
    echo.
    echo Setup failed. Review the message above.
    pause
    exit /b 1
  )
)

echo [Web Task Agent] Starting the Streamlit interface...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
if errorlevel 1 (
  echo.
  echo The app stopped with an error. Review the message above.
  pause
  exit /b 1
)

endlocal
