@echo off
chcp 65001 >nul
title Install Bridge Deps
setlocal EnableExtensions
cd /d "%~dp0"

REM 注意：本文件必须使用 CRLF 行尾，否则 cmd 会解析失败

set "PY=C:\Users\zuowe\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%PY%" (
  echo [ERR] python venv not found: %PY%
  pause
  exit /b 1
)

echo [INFO] installing: mcp websockets python-dotenv psutil ...
"%PY%" -m pip install -r requirements.txt

echo.
echo [INFO] running stdio selftest...
"%PY%" selftest_stdio.py

pause
