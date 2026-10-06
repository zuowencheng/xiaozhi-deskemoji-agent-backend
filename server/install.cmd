@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ============================================================
REM  小智自建后台 · 安装依赖
REM  使用免安装 Python（不污染系统、不需要管理员）
REM  注意：本文件必须使用 CRLF 行尾，否则 cmd 会解析失败
REM ============================================================

set "PY=C:\Users\zuowe\.workbuddy\binaries\python\versions\3.11.16\python\python.exe"
set "VENV=C:\Users\zuowe\.workbuddy\binaries\python\envs\xiaozhi-server"

if not exist "%PY%" (
    echo [错误] 找不到免安装 Python: %PY%
    echo         请重新运行 tools\bootstrap_python.py
    pause
    exit /b 1
)

if not exist "%~dp0requirements-base.txt" (
    echo [错误] 找不到依赖清单: %~dp0requirements-base.txt
    pause
    exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
    echo [1/3] 创建虚拟环境 %VENV%
    "%PY%" -m venv "%VENV%"
)

echo [2/3] 升级 pip
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel -i https://mirrors.aliyun.com/pypi/simple/

echo [3/3] 安装精简依赖（不装 torch/funasr，走云端 API）
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements-base.txt" -i https://mirrors.aliyun.com/pypi/simple/ --timeout 120 --retries 5

echo.
echo ------------------------------------------------------------
echo  安装完成。接下来：
echo   1. 复制 config.example.yaml 到 xiaozhi-server\data\.config.yaml 并填入 API Key
echo   2. 双击 start_server.cmd 启动
echo ------------------------------------------------------------
pause
