@echo off
chcp 65001 >nul
REM ============================================================
REM  小智桌面智能体 · 一键部署
REM
REM  双击即用：环境 -> 依赖 -> 配置 -> 开机自启 -> 启动 -> 体检
REM  幂等设计：装一半再跑一次不会搞坏，已完成的会自动跳过
REM
REM  参数（拖到本文件上，或在命令行里加）：
REM    无参数          完整部署
REM    --check-only    只体检不动手
REM    --skip-deps     跳过装依赖（依赖已装好时用，快）
REM ============================================================
setlocal

set PY=C:\Users\zuowe\.workbuddy\binaries\python\versions\3.13.12\python.exe
set SCRIPT=%~dp0setup.py

if not exist "%PY%" (
  echo [X] 没找到 Python: %PY%
  echo     请先装 Python 3.13 或改本文件里的 PY 变量
  pause
  exit /b 1
)

cd /d "%~dp0.."
"%PY%" "%SCRIPT%" %*

echo.
echo ============================================================
echo  跑完了。上面有 ✗ 的就是要处理的。
echo  日常排障先跑：python tools\doctor.py
echo ============================================================
pause
