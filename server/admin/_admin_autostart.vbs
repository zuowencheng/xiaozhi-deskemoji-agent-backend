' 小智后台管理台 · 开机静默启动器
' 由 autostart_on.cmd 复制到启动文件夹，作用是无窗口拉起管理台并接管后台服务。
' 第二个参数 0 = 隐藏窗口，False = 不等待返回。
' 路径不含空格，故不加引号；换路径时注意这一点。
Dim sh
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\server\admin"
sh.Run "C:\Users\zuowe\.workbuddy\binaries\python\versions\3.13.12\python.exe admin_server.py 8005 --autostart", 0, False
