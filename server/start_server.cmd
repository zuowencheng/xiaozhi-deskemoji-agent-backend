@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ============================================================
REM  小智自建后台 · 启动
REM  启动后窗口不要关闭，Ctrl+C 退出
REM  注意：本文件必须使用 CRLF 行尾，否则 cmd 会解析失败
REM ============================================================

set "VENV=C:\Users\zuowe\.workbuddy\binaries\python\envs\xiaozhi-server\Scripts\python.exe"
set "FFMPEG=C:\Users\zuowe\.workbuddy\binaries\ffmpeg\ffmpeg-master-latest-win64-gpl\bin"
set "SRV=%~dp0xiaozhi-server"

if not exist "%VENV%" (
    echo [错误] 虚拟环境不存在: %VENV%
    echo         请先运行 install.cmd 安装依赖
    pause
    exit /b 1
)

if not exist "%SRV%\app.py" (
    echo [错误] 找不到服务端入口: %SRV%\app.py
    pause
    exit /b 1
)

if exist "%FFMPEG%\ffmpeg.exe" (
    set "PATH=%FFMPEG%;%PATH%"
) else (
    echo [警告] 未找到 ffmpeg: %FFMPEG%\ffmpeg.exe
    echo        音频转码可能失败，但不影响启动
)

set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%SRV%"

echo ============================================================
echo  小智自建后台启动中...
echo  工作目录: %SRV%
echo  WebSocket : ws://本机IP:8000/xiaozhi/v1/
echo  OTA 接口  : http://本机IP:8003/xiaozhi/ota/
echo ============================================================
echo.

"%VENV%" app.py
set "RC=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
echo  服务已退出。退出码 = %RC%
if not "%RC%"=="0" echo  提示: 上方日志最后几行通常就是出错原因
echo ------------------------------------------------------------
pause
