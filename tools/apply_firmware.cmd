@echo off
chcp 65001 >nul
setlocal EnableExtensions
title 小智固件 - 编译并刷入 ota_1

REM ============================================================
REM  一键：编译固件 -> 写入 ota_1 -> 打印校验值
REM  本文件必须用 CRLF 行尾，否则 cmd 会把每行腰斩、双击一闪而过
REM
REM  本次固件改动（2026-10-02 21:30）：
REM    1. protocol.cc    : IsTimeout 120s -> 3600s（空闲不再误判通道超时）
REM    2. application.cc : idle 时每 5s 补一次长连接（接收服务端主动播报）
REM ============================================================

set "PROJ=C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent"
set "SRC=%PROJ%\firmware\community"
set "PROFILE=C:\Espressif\tools\Microsoft.v5.5.4.PowerShell_profile.ps1"
set "ESPTOOL=C:\Espressif\tools\python\v5.5.4\venv\Scripts\esptool.exe"
set "PORT=COM3"

echo ============================================================
echo   小智固件：编译 + 刷入 ota_1
echo   工程: %SRC%
echo   端口: %PORT%   ^(不碰 ota_0，随时可回滚^)
echo ============================================================
echo.

if not exist "%PROFILE%" (
    echo [错误] 找不到 IDF 激活脚本: %PROFILE%
    echo        先确认 ESP-IDF v5.5.4 装好了
    pause
    exit /b 1
)
if not exist "%ESPTOOL%" (
    echo [错误] 找不到 esptool: %ESPTOOL%
    pause
    exit /b 1
)

echo ============================================================
echo   第 1 步 / 2：编译固件（增量，约 1-3 分钟，请耐心等）
echo ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -Command ". '%PROFILE%'; Set-Location '%SRC%'; idf.py build; exit $LASTEXITCODE"
if errorlevel 1 goto :fail

if not exist "%SRC%\build\xiaozhi.bin" goto :nobin

echo.
echo ============================================================
echo   第 2 步 / 2：写入 ota_1 ^(0x500000^)
echo ============================================================
"%ESPTOOL%" --chip esp32s3 --port %PORT% --baud 460800 write_flash 0x500000 "%SRC%\build\xiaozhi.bin"
if errorlevel 1 goto :fail

echo.
echo ============================================================
echo   完成！下面这个 SHA256 前 9 位，应该和串口启动日志里的
echo   「ELF file SHA256: xxxxxxxx」对得上（注意：Compile time 不可信）
echo ============================================================
powershell -NoProfile -Command "$h=(Get-FileHash -Algorithm SHA256 '%SRC%\build\xiaozhi.elf').Hash; Write-Host ('本地 ELF SHA256 = ' + $h); Write-Host ('对比串口应显示  = ' + $h.Substring(0,9))"

echo.
echo ============================================================
echo   下一步：唤醒设备说「你好小智」
echo   - 设备应保持长连：服务端 /notify/status 的 online 里能看到它
echo   - 然后试：「三十秒后提醒我喝水」，到点应主动开口
echo.
echo   万一新版有问题，双击回滚：tools\rollback_to_ota0.cmd
echo ============================================================
pause
exit /b 0

:fail
echo.
echo [失败] 往上翻，最后的报错就是原因。窗口别关，截图发给峰哥。
pause
exit /b 1

:nobin
echo.
echo [失败] 编译声称成功，但没找到 %SRC%\build\xiaozhi.bin
pause
exit /b 1
