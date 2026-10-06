@echo off
chcp 65001 >nul
echo ==========================================
echo  小智固件回滚（只回滚 app 分区，约 10 秒）
echo ==========================================
"C:\Users\zuowe\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m esptool --port COM3 write-flash 0xD000 "%~dp0otadata.bin"
"C:\Users\zuowe\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m esptool --port COM3 write-flash 0x100000 "%~dp0ota_0_app.bin"
echo.
echo 回滚完成，按任意键重启设备查看效果
pause
