@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ============================================================
REM  小智后台管理台 · 启动
REM  地址 http://127.0.0.1:8005
REM  本进程由你启动 —— 它拉起的后台服务不会被沙箱回收，
REM  所以这是"后台常驻"的正解。
REM  注意：本文件必须 CRLF 行尾，改动后用 tools/fix_cmd_encoding.py 复查
REM ============================================================

set "PY="
for %%P in ("C:\Users\zuowe\.workbuddy\binaries\python\envs\xiaozhi-server\Scripts\python.exe" "C:\Users\zuowe\.workbuddy\binaries\python\versions\3.13.12\python.exe") do if not defined PY if exist %%P set "PY=%%~P"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY set "PY=python"

cd /d "%~dp0"

echo ============================================================
echo  小智后台管理台启动中...
echo  浏览器打开: http://127.0.0.1:8005
echo  这个窗口保持开着；关掉它后台服务不受影响。
echo ============================================================
echo.

"%PY%" admin_server.py 8005 --autostart
set "RC=%ERRORLEVEL%"

echo.
echo 管理台已退出。退出码 = %RC%
pause
