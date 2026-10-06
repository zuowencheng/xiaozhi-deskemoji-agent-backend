#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PoC-1 判定二：CEF 是否读取命令行参数（决定 CDP 是否彻底不可行）。"""
import glob
import os
import subprocess
import time
import urllib.request

ROOT_EXE = r"C:\Software\kugou\KGMusic\KuGou.exe"
VERDIR = r"C:\Software\kugou\KGMusic\20.1.51.27967"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sh(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=40)
        return ((r.stdout or b"") + (r.stderr or b"")).decode("gbk", "replace")
    except Exception as e:
        return "[err] %s" % e


def get(url, t=3.0):
    try:
        with OPENER.open(url, timeout=t) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def snapshot_before():
    s = {}
    for f in glob.glob(os.path.join(VERDIR, "*.log")) + glob.glob(os.path.join(VERDIR, "debug.log")):
        s[f] = os.path.getmtime(f)
    return s


def main():
    sh('taskkill /F /IM KuGou.exe /T')
    time.sleep(2)
    before = snapshot_before()
    print("[before] logs=%s" % list(before))

    args = [ROOT_EXE, "--remote-debugging-port=9222", "--enable-logging", "--v=1"]
    print("[launch] %s" % " ".join(args))
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, close_fds=True, creationflags=flags)
    time.sleep(25)

    tl = sh('tasklist /FI "IMAGENAME eq KuGou.exe" /NH')
    pids = set()
    for line in tl.splitlines():
        p = line.split()
        if len(p) >= 2 and p[0].lower().startswith("kugou"):
            pids.add(p[1])
    print("[pids] %s" % sorted(pids))

    print("[all listeners owned by KuGou]")
    ns = sh("netstat -ano -p tcp")
    hits = 0
    for line in ns.splitlines():
        if "LISTENING" in line.upper():
            parts = " ".join(line.split()).split()
            if len(parts) >= 5 and parts[-1] in pids:
                print("   %s  pid=%s" % (parts[1], parts[-1]))
                hits += 1
    if hits == 0:
        print("   (none)")

    print("[log files after]")
    after = snapshot_before()
    for f, t in after.items():
        tag = "NEW" if f not in before else ("MODIFIED" if t != before[f] else "same")
        print("   %-60s %s" % (os.path.basename(f), tag))
    if not after:
        print("   (no .log in version dir)")

    for port in (9222, 9223):
        st, body = get("http://127.0.0.1:%d/json/version" % port)
        print("[cdp %d] status=%s body=%s" % (port, st, body[:160]))

    print("\n[verdict] 若 BOTH 端口不监听 且 无新日志 -> CEF 未读取命令行参数 -> CDP 路线关闭，转 UI Automation")


if __name__ == "__main__":
    main()
