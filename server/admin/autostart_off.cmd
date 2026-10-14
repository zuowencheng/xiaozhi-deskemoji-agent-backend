@echo off
chcp 65001 >nul
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
del /f /q "%STARTUP%\XiaozhiAdmin.vbs" >nul 2>&1
if errorlevel 1 (
    echo 未找到自启文件，可能本来就没安装。
) else (
    echo 已取消开机自启。
)
echo.
echo 注意：这只会阻止"下次开机自动启动"，
echo       已经在跑的管理台需要你手动关掉那个窗口。
pause
