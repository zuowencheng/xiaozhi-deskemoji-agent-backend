#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PoC-1 诊断：酷狗（CEF）到底接受不接受 --remote-debugging-port。

一次性做完：列可执行文件 -> 带参数启动 -> 查进程/命令行/监听端口 -> 试 CDP 端点。
必须在**同一次执行**里完成（脱离回合的进程会被回收）。
"""
import glob
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

KGDIR = r"C:\Software\kugou\KGMusic"
VERDIR = os.path.join(KGDIR, "20.1.51.27967")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

FLAG = "--remote-debugging-port=9222"


def sh(cmd):
    try:
        out = subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
        enc = "gbk" if os.name == "nt" else "utf-8"
        return (out.stdout or b"").decode(enc, "replace") + (out.stderr or b"").decode(enc, "replace")
    except Exception as e:
        return "[sh err] %s: %s" % (type(e).__name__, e)


def get(url, timeout=3.0):
    try:
        with OPENER.open(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def listeners():
    txt = sh("netstat -ano -p tcp")
    rows = []
    for line in txt.splitlines():
        if "LISTENING" in line.upper():
            rows.append(" ".join(line.split()))
    return rows


def kill_kugou():
    for img in ("KuGou.exe",):
        sh('taskkill /F /IM %s /T' % img)
    time.sleep(2)


def launch(exe, extra=None):
    args = [exe, FLAG]
    if extra:
        args += extra
    print("[launch] %s" % " ".join(args))
    flags = 0
    for n in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, n, 0)
    p = subprocess.Popen(args, close_fds=True, creationflags=flags)
    print("[launch] pid=%s" % p.pid)
    return p


def report(tag):
    print("\n=== %s ===" % tag)
    print("[tasklist]")
    print(sh('tasklist /FI "IMAGENAME eq KuGou.exe" /NH').strip()[:1200])
    print("[cmdline of KuGou procs]")
    cl = sh('wmic process where "name=\'KuGou.exe\'" get ProcessId,CommandLine /format:list')
    if "[sh err]" in cl or len(cl.strip()) < 5:
        cl = sh('powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"name=\'KuGou.exe\'\\" | Select-Object ProcessId,CommandLine | Format-List"')
    print(cl.strip()[:2000])
    print("[listeners 92xx]")
    for r in listeners():
        parts = r.split()
        if len(parts) >= 2 and parts[1].endswith(("9222", "9223", "9229")):
            print("   " + r)
    st, body = get("http://127.0.0.1:9222/json/version")
    print("[cdp 9222] status=%s body=%s" % (st, body[:200]))


def main():
    print("### 0. 目录可执行文件")
    for d in (KGDIR, VERDIR):
        print("  %s:" % d)
        for f in sorted(glob.glob(os.path.join(d, "*.exe")))[:20]:
            print("    %-34s %8d B" % (os.path.basename(f), os.path.getsize(f)))

    print("\n### 1. 清理")
    kill_kugou()

    print("\n### 2. 试验 A：版本目录 exe + flag（flag 在前）")
    launch(os.path.join(VERDIR, "KuGou.exe"))
    time.sleep(22)
    report("A")

    print("\n### 3. 试验 B：根启动器 exe + flag")
    kill_kugou()
    launch(os.path.join(KGDIR, "KuGou.exe"))
    time.sleep(22)
    report("B")

    print("\n### 4. 试验 C：版本目录 exe + flag + allow-origins")
    kill_kugou()
    launch(os.path.join(VERDIR, "KuGou.exe"), ["--remote-allow-origins=*"])
    time.sleep(22)
    report("C")

    print("\n### 5. 结束（保留最后一次启动的进程，供人工观察）")


if __name__ == "__main__":
    main()
