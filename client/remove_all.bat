@echo off
chcp 65001 > nul
echo ========================================
echo   Flowtrace 一键取消
echo ========================================
echo.

echo [1/2] 注销计划任务...
call "%~dp0scripts\unregister_notification_tasks.bat"

echo.
echo [2/2] 移除开机自启动...
call "%~dp0scripts\remove_autostart.bat"

echo.
echo ========================================
echo   取消完成！
echo ========================================
pause
