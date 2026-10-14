# -*- coding: utf-8 -*-
"""
小智后台 · 开机自启安装器
------------------------------------------------------------
在**用户登录时**拉起看门狗 (tools/svc_watchdog.py)；看门狗再保证
8000(app.py) / 8005(admin) 常驻。

两种落地方式：
  · vbs  （默认，免管理员）—— 往「启动」文件夹放一个 VBS，登录时静默启动。
  · task （需权限）        —— 注册任务计划程序，可设失败重启/电池条件。

本机实测：沙箱执行 schtasks /create 会被「拒绝访问」拦下，所以默认走 vbs。

用法：
    python tools/install_autostart.py --install            # 安装（默认 vbs）
    python tools/install_autostart.py --install --method task
    python tools/install_autostart.py --uninstall
    python tools/install_autostart.py --status
    python tools/install_autostart.py --run                # 立即启动看门狗（免重启）
"""
import argparse
import os
import subprocess
import sys
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import paths  # 统一路径解析（tools/paths.py）：自发现 + xiaozhi.local.json 覆盖

ROOT = paths.ROOT
WATCHDOG = os.path.join(HERE, "svc_watchdog.py")

TASK_NAME = "XiaozhiSvcWatchdog"
# 静默解释器（pythonw）由 paths 解析：优先当前解释器的 pythonw 兄弟，其次 venv / PATH
PYW = paths.PYW_OR_PY

STARTUP_DIR = os.path.join(
    os.environ.get("APPDATA", ""),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
)
VBS_NAME = "XiaozhiSvcWatchdog.vbs"
VBS_PATH = os.path.join(STARTUP_DIR, VBS_NAME)

XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>小智后台看门狗：登录时静默启动，保证 8000/8005 常驻</Description>
    <URI>\\{task}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT15S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{pyw}</Command>
      <Arguments>"{script}"</Arguments>
      <WorkingDirectory>{cwd}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _run(args):
    r = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


# ---------------- 方式一：任务计划程序 ----------------
def cmd_status():
    code, out = _run(["schtasks", "/query", "/tn", TASK_NAME, "/v", "/fo", "LIST"])
    print("[计划任务] %s" % ("已注册" if code == 0 else "未注册"))
    if code == 0:
        print(out)
    print("[启动文件夹] %s -> %s" % (VBS_PATH, "已安装" if os.path.exists(VBS_PATH) else "未安装"))
    return code == 0


def cmd_install_task():
    xml_path = os.path.join(HERE, "_autostart_task.xml")
    xml = XML_TEMPLATE.format(
        task=escape(TASK_NAME), pyw=escape(PYW),
        script=escape(WATCHDOG), cwd=escape(HERE),
    )
    with open(xml_path, "w", encoding="utf-16", newline="\r\n") as f:
        f.write(xml)
    code, out = _run(["schtasks", "/create", "/tn", TASK_NAME, "/xml", xml_path, "/f"])
    print(out)
    if code != 0:
        print("[失败] 注册任务失败，退出码 =", code)
        return False
    print("[成功] 已注册计划任务: %s" % TASK_NAME)
    return True


def cmd_uninstall_task():
    code, out = _run(["schtasks", "/delete", "/tn", TASK_NAME, "/f"])
    print(out)
    print("[完成] 退出码 =", code)
    return code == 0


def cmd_run_task():
    code, out = _run(["schtasks", "/run", "/tn", TASK_NAME])
    print(out)
    print("[完成] 退出码 =", code)
    return code == 0


# ---------------- 方式二：启动文件夹 (VBS) ----------------
def _vbs_body():
    # VBS 字符串里每个字面引号要写成两个
    esc = '""%s"" ""%s""' % (PYW, WATCHDOG)
    return (
        "' Xiaozhi service watchdog - silent autostart at login\r\n"
        "' Launched by Windows at logon; keeps ports 8000/8005 alive.\r\n"
        "Dim sh\r\n"
        "Set sh = CreateObject(\"WScript.Shell\")\r\n"
        "sh.CurrentDirectory = \"%s\"\r\n"
        "sh.Run \"%s\", 0, False\r\n"
    ) % (HERE, esc)


def cmd_install_vbs():
    if not os.path.isdir(STARTUP_DIR):
        print("[错误] 找不到启动文件夹: %s" % STARTUP_DIR)
        return False
    try:
        with open(VBS_PATH, "w", encoding="ascii", newline="") as f:
            f.write(_vbs_body())
    except OSError as e:
        print("[失败] 写入失败: %s" % e)
        return False
    print("[成功] 已安装开机自启: %s" % VBS_PATH)
    print("        下次登录 Windows 后，看门狗会静默启动，自动拉起后台服务。")
    return True


def cmd_uninstall_vbs():
    if os.path.exists(VBS_PATH):
        try:
            os.remove(VBS_PATH)
        except OSError as e:
            print("[失败] 删除失败: %s" % e)
            return False
        print("[完成] 已移除开机自启: %s" % VBS_PATH)
    else:
        print("[跳过] 未安装: %s" % VBS_PATH)
    return True


def cmd_run_vbs():
    """借 explorer.exe 立刻启动看门狗（脱离本会话 job，能常驻）。"""
    cmd_path = os.path.join(HERE, "_watchdog_up.cmd")
    lines = ["@echo off", 'start "" "%s" "%s"' % (PYW, WATCHDOG)]
    with open(cmd_path, "w", encoding="ascii", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    try:
        subprocess.run(["explorer.exe", cmd_path], capture_output=True, timeout=20)
    except Exception as e:
        print("[失败] explorer 拉起失败: %s" % e)
        return False
    print("[完成] 已借 explorer 启动看门狗（见 server/_logs/watchdog.log）")
    return True


# ---------------- 入口 ----------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", choices=["task", "vbs"], default="vbs")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--run", action="store_true")
    args = ap.parse_args()

    if not any([args.install, args.uninstall, args.status, args.run]):
        ap.print_help()
        return

    if not os.path.exists(PYW):
        print("[错误] 找不到 pythonw: %s" % PYW)
        sys.exit(1)
    if not os.path.exists(WATCHDOG):
        print("[错误] 找不到看门狗脚本: %s" % WATCHDOG)
        sys.exit(1)

    if args.status:
        cmd_status()
    if args.install:
        cmd_install_vbs() if args.method == "vbs" else cmd_install_task()
    if args.run:
        cmd_run_vbs() if args.method == "vbs" else cmd_run_task()
    if args.uninstall:
        cmd_uninstall_vbs() if args.method == "vbs" else cmd_uninstall_task()


if __name__ == "__main__":
    main()
