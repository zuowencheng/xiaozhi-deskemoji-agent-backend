# -*- coding: utf-8 -*-
"""端到端验证：播报接口 / 取件 watcher / 定时提醒。"""
import glob
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PROJECT = r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent"
DONE = os.path.join(PROJECT, "bridge", "done")
ANN = os.path.join(PROJECT, "bridge", "announced")


def req(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(r, timeout=6) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


print("=== 1. 播报接口状态 ===")
print("  GET /notify/status ->", req("GET", "http://127.0.0.1:8004/notify/status"))

print("\n=== 2. 取件 watcher：造一个已完成任务 ===")
tid = "e2e-" + datetime.now().strftime("%H%M%S")
f = os.path.join(DONE, f"task-{tid}.md")
with open(f, "w", encoding="utf-8") as fh:
    fh.write(f"# 任务 {tid}\n\n- 任务编号：{tid}\n- 创建时间：x\n- 来源：语音（小智 deskemoji）\n"
             f"- 状态：done\n\n## 任务内容\n\n自检：把某个测试开关关掉\n\n## 执行结果\n\n已经关掉了。\n")
print("  已写入:", os.path.basename(f))

print("\n=== 3. 定时提醒：设一条 40 秒后的 ===")
at = (datetime.now() + timedelta(seconds=40)).strftime("%Y-%m-%dT%H:%M")
print("  POST /remind ->", req("POST", "http://127.0.0.1:8004/remind",
                              {"text": "自检提醒：喝口水", "at": at}))
print("  GET /remind/list ->", req("GET", "http://127.0.0.1:8004/remind/list"))

print("\n=== 4. 等 45 秒，看 watcher 与提醒是否触发 ===")
time.sleep(45)

log = os.path.join(PROJECT, "server", "xiaozhi-server", "_e2e_server.log")
with open(log, "r", encoding="utf-8", errors="replace") as fh:
    lines = fh.readlines()
hits = [l.strip() for l in lines if any(k in l for k in ("取件", "提醒", "播报"))]
print("  相关日志（最近 12 条）:")
for l in hits[-12:]:
    print("   ", l[:150])

print("\n=== 5. 标记与提醒状态 ===")
print("  announced 里有没有", tid, ":", os.path.exists(os.path.join(ANN, f"{tid}.mark")))
print("  剩余提醒:", req("GET", "http://127.0.0.1:8004/remind/list")[1][:200])

# 清理
try:
    os.remove(f)
    p = os.path.join(ANN, f"{tid}.mark")
    if os.path.exists(p):
        os.remove(p)
    print("\n  已清理自检任务")
except Exception as e:
    print("  清理失败:", e)
