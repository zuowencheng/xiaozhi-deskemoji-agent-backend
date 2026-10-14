@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ============================================================
REM  小智自建后台 · 安装依赖
REM  自动检测 Python，虚拟环境建在 server\.venv （项目内，跟着仓库走）
REM  注意：本文件必须使用 CRLF 行尾，否则 cmd 会解析失败
REM ============================================================

REM ---- 找一个可用的 Python（优先 py 启动器，其次 PATH）----
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY where python3 >nul 2>&1 && set "PY=python3"

if not defined PY (
    echo [错误] 没找到 Python。
    echo         请先安装 Python 3.10+ : https://www.python.org/downloads/
    echo         安装时务必勾选 "Add python.exe to PATH"
    pause
    exit /b 1
)

set "VENV=%~dp0.venv"

if not exist "%~dp0requirements-base.txt" (
    echo [错误] 找不到依赖清单: %~dp0requirements-base.txt
    pause
    exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
    echo [1/3] 创建虚拟环境 %VENV%
    %PY% -m venv "%VENV%"
)

echo [2/3] 升级 pip
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel -i https://mirrors.aliyun.com/pypi/simple/

echo [3/3] 安装精简依赖（不装 torch/funasr，走云端 API）
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements-base.txt" -i https://mirrors.aliyun.com/pypi/simple/ --timeout 120 --retries 5

echo.
echo ------------------------------------------------------------
echo  安装完成。接下来：
echo   1. 复制 config.example.yaml 到 xiaozhi-server\data\.config.yaml 并填入 API Key
echo   2. 双击 deploy\一键部署.cmd 启动（或缺省直接用 start_server.cmd）
echo ------------------------------------------------------------
pause
