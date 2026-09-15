@echo off
chcp 65001 > nul
echo ========================================
echo   Flowtrace 一键配置
echo ========================================
echo.

echo [1/2] 注册计划任务...
call "%~dp0scripts\register_notification_tasks.bat"

echo.
echo [2/2] 设置开机自启动...
call "%~dp0scripts\setup_autostart.bat"

echo.
echo ========================================
echo   配置完成！
echo ========================================
pause
