@echo off
chcp 65001 >nul
REM ============================================================
REM  小智桌面智能体 · 一键部署
REM
REM  双击即用：环境 -> 依赖 -> 配置 -> 开机自启 -> 启动 -> 体检
REM  幂等设计：装一半再跑一次不会搞坏，已完成的会自动跳过
REM  Python 自动检测：项目内 venv -> py 启动器 -> PATH 上的 python
REM
REM  参数（拖到本文件上，或在命令行里加）：
REM    无参数          完整部署
REM    --check-only    只体检不动手
REM    --skip-deps     跳过装依赖（依赖已装好时用，快）
REM ============================================================
setlocal EnableExtensions

set "PY="
if exist "%~dp0..\server\.venv\Scripts\python.exe" set "PY=%~dp0..\server\.venv\Scripts\python.exe"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY where python3 >nul 2>&1 && set "PY=python3"

if not defined PY (
  echo [X] 没找到 Python。
  echo     请先安装 Python 3.10+ : https://www.python.org/downloads/
  echo     安装时务必勾选 "Add python.exe to PATH"
  pause
  exit /b 1
)

set SCRIPT=%~dp0setup.py

cd /d "%~dp0.."
%PY% "%SCRIPT%" %*

echo.
echo ============================================================
echo  跑完了。上面有 X 的就是要处理的。
echo  日常排障先跑：python tools\doctor.py
echo ============================================================
pause
