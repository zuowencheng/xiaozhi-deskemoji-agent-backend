@echo off
chcp 65001 >nul
title XiaoZhi Desk Bridge
setlocal EnableExtensions
cd /d "%~dp0"

REM 注意：本文件必须使用 CRLF 行尾，否则 cmd 会解析失败

set "PY=C:\Users\zuowe\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%PY%" (
  echo [ERR] python venv not found: %PY%
  echo [ERR] run install.cmd first
  pause
  exit /b 1
)

if not exist "%~dp0server_agent.py" (
  echo [ERR] server_agent.py not found in %~dp0
  pause
  exit /b 1
)

echo [INFO] starting bridge, keep this window open...
"%PY%" mcp_pipe.py server_agent.py
set "RC=%ERRORLEVEL%"
echo.
echo [INFO] bridge exited, code = %RC%
pause
