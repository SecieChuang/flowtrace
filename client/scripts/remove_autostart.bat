@echo off
setlocal
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET=%STARTUP%\Flowtrace.cmd"

if exist "%TARGET%" (
    del /f /q "%TARGET%"
    echo.
    echo Flowtrace startup shortcut removed.
    echo.
) else (
    echo.
    echo Flowtrace startup shortcut not found.
    echo.
)
pause
