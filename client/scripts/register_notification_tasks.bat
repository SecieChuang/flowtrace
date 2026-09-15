@echo off
powershell.exe -ExecutionPolicy Bypass -File "%~dp0register_notification_tasks.ps1"
if errorlevel 1 (
  echo.
  echo Task registration failed. Check the error above.
  pause
  exit /b 1
)
echo.
echo Notification tasks registered successfully.
pause
