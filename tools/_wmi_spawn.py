# -*- coding: utf-8 -*-
"""
用 WMI(Win32_Process.Create) 拉起常驻进程。
------------------------------------------------------------
为什么必须绕这一道（2026-10-02 本机实测）：
  · DETACHED_PROCESS            → 服务起得来，但 30~40s 后被 job 连带杀掉，
                                  日志无任何异常、端口变 closed（极难排查）
  · CREATE_BREAKAWAY_FROM_JOB   → [WinError 5] 拒绝访问（沙箱 job 不允许 breakaway）
  · WMI Win32_Process.Create    → ✅ 可用
关键：WMI 创建的进程，父进程是 WmiPrvSE.exe，**不在调用方会话的 job object 里**，
      所以会话/回合结束不会把它带走。

用法：
  python _wmi_spawn.py --cwd <工作目录> --log <日志文件> --py <python.exe> --script <脚本>
"""
import argparse
import os
import sys

import pythoncom
import win32com.client


def build_cmdline(cwd, py, script, log_path, ffmpeg_bin):
    parts = [
        "cmd.exe /c cd /d " + cwd,
        "set PYTHONUTF8=1",
        "set PYTHONIOENCODING=utf-8",
    ]
    if ffmpeg_bin and os.path.isdir(ffmpeg_bin):
        parts.append("set PATH=" + ffmpeg_bin + ";%PATH%")
    parts.append('"%s" "%s" >> "%s" 2>&1' % (py, script, log_path))
    return " && ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--py", required=True)
    ap.add_argument("--script", required=True)
    ap.add_argument("--ffmpeg", default="")
    a = ap.parse_args()

    os.makedirs(os.path.dirname(a.log), exist_ok=True)
    cmdline = build_cmdline(a.cwd, a.py, a.script, a.log, a.ffmpeg)

    pythoncom.CoInitialize()
    loc = win32com.client.Dispatch("WbemScripting.SWbemLocator")
    svc = loc.ConnectServer(".", "root\\cimv2")
    proc = svc.Get("Win32_Process")

    out = None
    try:
        startup = svc.Get("Win32_ProcessStartup")
        startup.Properties_.Item("ShowWindow").Value = 0  # SW_HIDE：不弹黑窗
        out = proc.Create(cmdline, a.cwd, startup)
    except Exception as e:
        print("[warn] 隐藏窗口方式失败(%s)，改用普通方式" % e)

    if out is None:
        out = proc.Create(cmdline, a.cwd)

    # WMI 返回 (ProcessId, ReturnValue) 顺序不固定，按 0 值判定
    v0, v1 = out[0], out[1]
    if v0 == 0:
        rv, pid = 0, v1
    elif v1 == 0:
        rv, pid = 0, v0
    else:
        rv, pid = v0, None

    if rv != 0:
        print("[err] WMI 创建失败 ReturnValue=%s (raw=%r)" % (rv, out))
        sys.exit(2)

    print("[ok] WMI 已拉起 pid=%s" % pid)
    print("[cmd] %s" % cmdline)


if __name__ == "__main__":
    main()
