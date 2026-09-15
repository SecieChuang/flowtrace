@echo off
rem 先用 pythonw 后台运行；失败时改用 python 重跑以便看到错误信息
pythonw "%~dp0..\src\open_summary.py"
if %errorlevel%==0 exit /b 0
echo.
echo pythonw 运行失败（退出码 %errorlevel%），改用 python 重跑以显示错误：
echo.
python "%~dp0..\src\open_summary.py"
pause
