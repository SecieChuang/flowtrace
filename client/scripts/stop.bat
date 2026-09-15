@echo off
REM Stop any AutoHotkey process started with flowtrace.ahk in its command line.
taskkill /F /FI "IMAGENAME eq AutoHotkey*.exe" /FI "WINDOWTITLE eq *flowtrace*" >nul 2>&1
REM Fallback: match command-line arguments via PowerShell.
powershell -Command "Get-Process | Where-Object { $_.ProcessName -like 'AutoHotkey*' -and $_.MainModule.FileName -and (Get-CimInstance Win32_Process -Filter \"ProcessId=$($_.Id)\").CommandLine -like '*flowtrace*' } | Stop-Process -Force" 2>nul
echo Flowtrace stopped.
