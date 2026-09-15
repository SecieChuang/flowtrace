@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR:~0,-1%") do set "SCRIPT_DIR=%%~fI"

set "SCRIPTS_DIR=%SCRIPT_DIR%"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET=%STARTUP%\Flowtrace.cmd"

> "%TARGET%" echo @echo off
>> "%TARGET%" echo cd /d "%SCRIPTS_DIR%"
>> "%TARGET%" echo start "" /b "%SCRIPTS_DIR%\flowtrace.ahk"

echo.
echo Flowtrace startup shortcut created.
echo Launcher: %TARGET%
echo.
pause
