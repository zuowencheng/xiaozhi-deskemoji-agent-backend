@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM 把隐藏启动器放进"启动"文件夹 —— 下次登录 Windows 自动拉起管理台
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

if not exist "%STARTUP%" (
    echo [错误] 找不到启动文件夹：
    echo        %STARTUP%
    pause
    exit /b 1
)

copy /Y "%~dp0_admin_autostart.vbs" "%STARTUP%\XiaozhiAdmin.vbs" >nul
if errorlevel 1 (
    echo [错误] 复制失败，可能权限不足。
    pause
    exit /b 1
)

echo ============================================================
echo  已安装开机自启
echo    启动文件夹: %STARTUP%
echo    文件:       XiaozhiAdmin.vbs
echo ============================================================
echo.
echo  下次登录 Windows 后：
echo    - 管理台会在后台静默启动（没有窗口）
echo    - 同时自动把小智后台服务一并拉起
echo    - 直接用浏览器打开 http://127.0.0.1:8005
echo.
echo  想取消：双击 autostart_off.cmd
echo.
pause
