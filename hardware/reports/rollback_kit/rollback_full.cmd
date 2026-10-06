@echo off
chcp 65001 >nul
echo ==========================================
echo  小智全片回滚（16MB，约 2~3 分钟，救砖用）
echo ==========================================
"C:\Users\zuowe\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m esptool --port COM3 write-flash 0x0 "C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\hardware\reports\backup_20261002_16MB.bin"
echo.
echo 全片恢复完成
pause
