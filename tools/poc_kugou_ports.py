#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""PoC-1 判定三：探测酷狗自开的本地端口（8020 等）是否是可用 API。"""
import os
import socket
import subprocess
import time
import urllib.request

ROOT_EXE = r"C:\Software\kugou\KGMusic\KuGou.exe"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sh(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=40)
        return ((r.stdout or b"") + (r.stderr or b"")).decode("gbk", "replace")
    except Exception as e:
        return "[err] %s" % e


def http(url, t=3.0):
    try:
        with OPENER.open(url, timeout=t) as r:
            return r.status, dict(r.headers), r.read(600).decode("utf-8", "replace")
    except Exception as e:
        return None, {}, "%s: %s" % (type(e).__name__, e)


def main():
    sh('taskkill /F /IM KuGou.exe /T')
    time.sleep(2)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([ROOT_EXE], close_fds=True, creationflags=flags)
    time.sleep(20)

    ns = sh("netstat -ano -p tcp")
    addrs = []
    for line in ns.splitlines():
        if "LISTENING" in line.upper():
            p = " ".join(line.split()).split()
            if len(p) >= 5:
                addrs.append((p[1], p[-1]))
    print("[listeners]\n  " + "\n  ".join("%s pid=%s" % a for a in addrs))

    ports = []
    for a, pid in addrs:
        host, _, port = a.rpartition(":")
        if host.startswith(("127.", "0.0.0.0")) and port.isdigit():
            ports.append((int(port), a))
    ports.sort()

    # 只探高位/可疑端口，避免狂打系统端口
    cand = [(p, a) for p, a in ports if p not in (135, 139, 445, 5040, 8000, 8003, 8004, 8005)]
    print("\n[candidates] %s" % [a for _, a in cand])

    for port, a in cand[:14]:
        s = socket.socket()
        s.settimeout(1.5)
        try:
            s.connect(("127.0.0.1", port))
            tcp = "TCP-OK"
        except Exception as e:
            tcp = "tcp-%s" % type(e).__name__
        finally:
            s.close()
        st, hdrs, body = http("http://127.0.0.1:%d/" % port)
        srv = hdrs.get("Server", "")
        print("  :%-6d %-10s http=%s server=%s body=%s" % (port, tcp, st, srv, body[:110].replace("\n", " ")))
        if st == 200:
            for path in ("/api", "/api/", "/status", "/version", "/json/version", "/v1", "/info"):
                st2, _, b2 = http("http://127.0.0.1:%d%s" % (port, path), t=1.5)
                if st2:
                    print("       %s -> %s %s" % (path, st2, b2[:100].replace("\n", " ")))


if __name__ == "__main__":
    main()
