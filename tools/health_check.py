#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""体检：服务端口 / 进程 / 主动播报在线设备 / 串口设备。"""
import json
import os
import socket
import subprocess
import urllib.request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕开本机代理


def sh(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=30)
        return ((r.stdout or b"") + (r.stderr or b"")).decode("gbk", "replace")
    except Exception as e:
        return "[err] %s" % e


def http(url, t=3.0):
    try:
        with OPENER.open(url, timeout=t) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def port_open(p):
    s = socket.socket()
    s.settimeout(1.2)
    try:
        s.connect(("127.0.0.1", p))
        return True
    except Exception:
        return False
    finally:
        s.close()


def main():
    print("=== 1. 端口 ===")
    for p, name in [(8000, "WS 语音"), (8003, "OTA+视觉"), (8004, "主动播报"), (8005, "管理台")]:
        print("  %-5d %-10s %s" % (p, name, "OPEN" if port_open(p) else "closed"))

    print("\n=== 2. 相关进程 ===")
    tl = sh('tasklist /NH')
    for line in tl.splitlines():
        low = line.lower()
        if any(k in low for k in ("python", "ollama")):
            print("  " + " ".join(line.split()[:5]))

    print("\n=== 3. 主动播报状态 ===")
    st, body = http("http://127.0.0.1:8004/notify/status")
    print("  /notify/status -> %s %s" % (st, body[:300]))

    print("\n=== 4. 管理台 ===")
    st, body = http("http://127.0.0.1:8005/api/status")
    print("  /api/status -> %s %s" % (st, body[:400]))

    print("\n=== 5. 串口设备 ===")
    print("  " + sh('powershell -NoProfile -Command "[System.IO.Ports.SerialPort]::GetPortNames() -join \',\'"').strip())

    print("\n=== 6. 投递箱 ===")
    base = r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\bridge"
    for sub in ("inbox", "done", "announced"):
        d = os.path.join(base, sub)
        n = len(os.listdir(d)) if os.path.isdir(d) else -1
        print("  %-10s %s (%d 项)" % (sub, "OK" if n >= 0 else "缺", n))


if __name__ == "__main__":
    main()
