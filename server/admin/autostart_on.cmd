@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ============================================================
REM  小智后台 · 安装开机自启
REM  机制：登录时静默启动「看门狗」(tools\svc_watchdog.py)，
REM        看门狗负责保证 8000(app) / 8005(admin) 两个端口常驻，
REM        谁挂了自己补谁。
REM  注意：本文件必须 CRLF 行尾，改动后用 tools\fix_cmd_encoding.py 复查
REM ============================================================

set "PY=C:\Users\zuowe\.workbuddy\binaries\python\versions\3.13.12\python.exe"
set "ROOT=%~dp0..\.."

if not exist "%PY%" (
    echo [错误] 找不到 Python: %PY%
    pause
    exit /b 1
)

echo ============================================================
echo  正在安装「开机自启 + 服务自愈」
echo ============================================================
echo.

"%PY%" "%ROOT%\tools\install_autostart.py" --install --method vbs
echo.
"%PY%" "%ROOT%\tools\install_autostart.py" --status

echo.
echo 想取消：双击 autostart_off.cmd
pause
