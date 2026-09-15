@echo off
powershell.exe -ExecutionPolicy Bypass -File "%~dp0unregister_notification_tasks.ps1"
if errorlevel 1 (
  echo.
  echo Task removal failed. Check the error above.
  pause
  exit /b 1
)
echo.
echo Notification tasks removed successfully.
pause
