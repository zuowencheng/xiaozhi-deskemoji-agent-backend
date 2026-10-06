#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PoC-1：验证酷狗（CEF 内核）能否接受 --remote-debugging-port，从而暴露 CDP 端点。

用法：
    python tools/poc_kugou_cdp.py            # 启动酷狗（带调试端口）并探测
    python tools/poc_kugou_cdp.py --probe    # 只探测，不启动
    python tools/poc_kugou_cdp.py --port 9333

判定：
    /json/version 返回 JSON（含 Browser / WebKit-Version）→ CDP 可用 → 点歌走 CDP 路线
    端口无监听 / 连接被拒 → 退 UI Automation 路线
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

VERSIONED_EXE = r"C:\Software\kugou\KGMusic\20.1.51.27967\KuGou.exe"
LAUNCHER_EXE = r"C:\Software\kugou\KGMusic\KuGou.exe"
EXE = VERSIONED_EXE if os.path.exists(VERSIONED_EXE) else LAUNCHER_EXE
DEFAULT_PORT = 9222


# 本机设了 HTTP 代理，urllib 会把 127.0.0.1 也送进代理 -> 得到 502 假象。
# 必须显式绕过代理，否则会误判成"CDP 不可用"。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_get(url, timeout=3.0):
    try:
        with _OPENER.open(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def sock_check(port, timeout=2.0):
    """裸 socket：确认端口到底有没有人在听。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True, "TCP connect OK"
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)
    finally:
        s.close()


def probe(port):
    ok_sock, msg = sock_check(port)
    print("[probe] TCP 127.0.0.1:%d -> %s" % (port, msg))
    print("[probe] http://127.0.0.1:%d/json/version" % port)
    st, body = http_get("http://127.0.0.1:%d/json/version" % port)
    print("  -> status=%s" % st)
    print("  -> body=%s" % body[:600])
    ok = st == 200 and body.strip().startswith("{")
    if ok:
        try:
            info = json.loads(body)
            print("  -> Browser   : %s" % info.get("Browser"))
            print("  -> Protocol  : %s" % info.get("Protocol-Version"))
        except Exception:
            pass
    print("[probe] http://127.0.0.1:%d/json/list" % port)
    st2, body2 = http_get("http://127.0.0.1:%d/json/list" % port)
    print("  -> status=%s" % st2)
    if st2 == 200:
        try:
            targets = json.loads(body2)
            print("  -> %d target(s)" % len(targets))
            for t in targets[:10]:
                print("     [%s] %s  url=%s" % (t.get("type"), (t.get("title") or "")[:40], (t.get("url") or "")[:90]))
        except Exception as e:
            print("  -> parse err: %s" % e)
            print("  -> raw: %s" % body2[:600])
    else:
        print("  -> body=%s" % str(body2)[:300])
    return ok


def is_running():
    try:
        out = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq KuGou.exe", "/NH"],
                                      stderr=subprocess.STDOUT, timeout=15)
        text = out.decode("gbk", "replace")
        return "KuGou.exe" in text
    except Exception as e:
        print("[warn] tasklist failed: %s" % e)
        return None


def launch():
    print("[launch] %s --remote-debugging-port=%d" % (EXE, PORT))
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= getattr(subprocess, name, 0)
    p = subprocess.Popen([EXE, "--remote-debugging-port=%d" % PORT],
                         close_fds=True, creationflags=flags)
    print("[launch] pid=%s" % p.pid)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--probe", action="store_true", help="只探测不启动")
    ap.add_argument("--wait", type=int, default=20, help="启动后等待秒数")
    args = ap.parse_args()
    global PORT
    PORT = args.port

    print("exe exists : %s (%s)" % (os.path.exists(EXE), EXE))

    if args.probe:
        ok = probe(PORT)
        print("\n[verdict] CDP %s" % ("AVAILABLE" if ok else "NOT AVAILABLE"))
        return 0 if ok else 2

    running = is_running()
    print("KuGou already running: %s" % running)
    if running:
        print("[note] 酷狗已在运行，命令行参数不会生效。需先退出酷狗再重跑本脚本。")
        ok = probe(PORT)
        print("\n[verdict] CDP %s (现有进程)" % ("AVAILABLE" if ok else "NOT AVAILABLE"))
        return 0 if ok else 2

    launch()
    print("[wait] %ds ..." % args.wait)
    ok = False
    deadline = time.time() + args.wait
    while time.time() < deadline:
        time.sleep(2)
        st, _ = http_get("http://127.0.0.1:%d/json/version" % PORT, timeout=2)
        if st == 200:
            ok = True
            break
    print("")
    ok = probe(PORT) or ok
    print("\n[verdict] CDP %s" % ("AVAILABLE" if ok else "NOT AVAILABLE"))
    print("[hint] 若 NOT AVAILABLE，可再试 Launcher：%s" % LAUNCHER_EXE)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
