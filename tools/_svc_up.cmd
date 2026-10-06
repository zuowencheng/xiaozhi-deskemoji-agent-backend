@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"C:\Users\zuowe\.workbuddy\binaries\python\versions\3.13.12\python.exe" "%~dp0start_services.py" --inner --wait 0 > "C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\server\_logs\launcher.out.log" 2>&1
