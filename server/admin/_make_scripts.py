"""生成管理台的外壳脚本（.cmd / .vbs）。

为什么用一个生成器：Windows 下 .cmd 必须同时满足
  1) UTF-8 编码 + chcp 65001（否则中文乱码）
  2) CRLF 行尾（否则 cmd 把每行腰斩，双击一闪而过）
用 Write 工具直接落盘是 LF，手工转容易被忘掉，所以统一在这里出。
"""
from pathlib import Path
import sys

ADMIN_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ADMIN_DIR.parent.parent

# 路径统一走 tools/paths.py（自发现 + xiaozhi.local.json 覆盖）
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
import paths  # noqa: E402

# 候选解释器：优先服务 venv，其次基础 Python（生成的脚本里逐个探测存在的那个）
PY_CANDIDATES = [p for p in (paths.VENV_PY, paths.BASE_PY) if p]
# 保证至少有 2 个槽位，避免 .format(py1=, py2=) 越界
while len(PY_CANDIDATES) < 2:
    PY_CANDIDATES.append(PY_CANDIDATES[0] if PY_CANDIDATES else "python")

START_ADMIN = """@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ============================================================
REM  小智后台管理台 · 启动
REM  地址 http://127.0.0.1:8005
REM  本进程由你启动 —— 它拉起的后台服务不会被沙箱回收，
REM  所以这是"后台常驻"的正解。
REM  注意：本文件必须 CRLF 行尾，改动后用 tools/fix_cmd_encoding.py 复查
REM ============================================================

set "PY="
for %%P in ("{py1}" "{py2}") do if not defined PY if exist %%P set "PY=%%~P"
if not defined PY where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY set "PY=python"

cd /d "%~dp0"

echo ============================================================
echo  小智后台管理台启动中...
echo  浏览器打开: http://127.0.0.1:8005
echo  这个窗口保持开着；关掉它后台服务不受影响。
echo ============================================================
echo.

"%PY%" admin_server.py 8005 --autostart
set "RC=%ERRORLEVEL%"

echo.
echo 管理台已退出。退出码 = %RC%
pause
""".format(py1=PY_CANDIDATES[0], py2=PY_CANDIDATES[1])

AUTOSTART_ON = """@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM 把隐藏启动器放进"启动"文件夹 —— 下次登录 Windows 自动拉起管理台
set "STARTUP=%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup"

if not exist "%STARTUP%" (
    echo [错误] 找不到启动文件夹：
    echo        %STARTUP%
    pause
    exit /b 1
)

copy /Y "%~dp0_admin_autostart.vbs" "%STARTUP%\\XiaozhiAdmin.vbs" >nul
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
""".format()

AUTOSTART_OFF = """@echo off
chcp 65001 >nul
set "STARTUP=%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup"
del /f /q "%STARTUP%\\XiaozhiAdmin.vbs" >nul 2>&1
if errorlevel 1 (
    echo 未找到自启文件，可能本来就没安装。
) else (
    echo 已取消开机自启。
)
echo.
echo 注意：这只会阻止"下次开机自动启动"，
echo       已经在跑的管理台需要你手动关掉那个窗口。
pause
""".format()

# 注意：这里刻意不给 python 路径加引号 —— VBS 里 """ 转义极易写错，
# 而这两个候选路径都不含空格，直接裸写即可。若将来换到含空格的路径，
# 必须改成 """<path>"" 的形式。
_PY_EXISTS = next((p for p in PY_CANDIDATES if Path(p).exists()), PY_CANDIDATES[0])
AUTOSTART_VBS = """' 小智后台管理台 · 开机静默启动器
' 由 autostart_on.cmd 复制到启动文件夹，作用是无窗口拉起管理台并接管后台服务。
' 第二个参数 0 = 隐藏窗口，False = 不等待返回。
' 路径不含空格，故不加引号；换路径时注意这一点。
Dim sh
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "{admin}"
sh.Run "{py} admin_server.py 8005 --autostart", 0, False
""".format(admin=str(ADMIN_DIR), py=_PY_EXISTS)

FILES = {
    "start_admin.cmd": START_ADMIN,
    "autostart_on.cmd": AUTOSTART_ON,
    "autostart_off.cmd": AUTOSTART_OFF,
    "_admin_autostart.vbs": AUTOSTART_VBS,
}

for name, body in FILES.items():
    p = ADMIN_DIR / name
    body = body.replace("\r\n", "\n").replace("\n", "\r\n")
    p.write_bytes(body.encode("utf-8"))
    raw = p.read_bytes()
    crlf = raw.count(b"\r\n")
    bare = raw.count(b"\n") - crlf
    print("  %-24s %6d 字节  CRLF=%d 裸LF=%d  %s" % (
        name, len(raw), crlf, bare, "OK" if bare == 0 else "!! 有裸 LF"))
print("生成完毕，目录: %s" % ADMIN_DIR)
