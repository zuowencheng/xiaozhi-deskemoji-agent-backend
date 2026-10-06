@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ============================================================
REM  小智 deskemoji · 一键回滚到 ota_0（原厂固件）
REM  原理：擦除 otadata 的 slot1，bootloader 只剩 slot0(seq=1) 有效
REM        → 自动切回 ota_0
REM  注意：本文件必须使用 CRLF 行尾
REM ============================================================

set "ES=C:\Espressif\tools\python\v5.5.4\venv\Scripts\esptool.exe"
set "PORT=COM3"

if not exist "%ES%" (
    echo [错误] 找不到 esptool: %ES%
    echo         请确认 ESP-IDF v5.5.4 仍安装在原位
    pause
    exit /b 1
)

echo ============================================================
echo  即将把设备启动分区切回 ota_0（原厂固件）
echo  设备: %PORT%   操作: 擦除 otadata slot1 (flash 0xE000)
echo ============================================================
echo.
set /p CONFIRM=确认继续? 输入 y 回车:
if /i not "%CONFIRM%"=="y" (
    echo 已取消。
    pause
    exit /b 0
)

echo.
"%ES%" --chip esp32s3 --port %PORT% --baud 460800 erase_region 0xE000 0x1000
set "RC=%ERRORLEVEL%"

echo.
echo ------------------------------------------------------------
if "%RC%"=="0" (
    echo  回滚完成。设备应已复位并启动 ota_0。
    echo  若没自动重启，按一下板上 RST / 重新插拔 USB。
) else (
    echo  回滚命令退出码 = %RC%，请检查上方输出。
)
echo ------------------------------------------------------------
pause
