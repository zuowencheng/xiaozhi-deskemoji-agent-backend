@echo off
chcp 65001 >nul
echo 串口监视中（115200），Ctrl+] 退出。用来判断新固件有没有正常启动。
"C:\Users\zuowe\.workbuddy\binaries\python\envs\default\Scripts\python.exe" -m serial.tools.miniterm COM3 115200
