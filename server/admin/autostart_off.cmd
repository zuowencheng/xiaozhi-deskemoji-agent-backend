@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ============================================================
REM  小智后台 · 取消开机自启
REM  只移除登录自启；已经在跑的看门狗/服务不受影响，
REM  需要的话用管理台或任务管理器结束。
REM  注意：本文件必须 CRLF 行尾
REM ============================================================

set "PY=C:\Users\zuowe\.workbuddy\binaries\python\versions\3.13.12\python.exe"
set "ROOT=%~dp0..\.."

"%PY%" "%ROOT%\tools\install_autostart.py" --uninstall --method vbs
echo.
"%PY%" "%ROOT%\tools\install_autostart.py" --status

echo.
echo 注意：这只会阻止「下次登录自动启动」。
echo       当前正在跑的看门狗进程需要你手动结束（任务管理器里找 pythonw.exe）。
pause
