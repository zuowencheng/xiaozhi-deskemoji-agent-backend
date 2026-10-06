# -*- coding: utf-8 -*-
"""验证 notify_plugin 的取件 watcher 与定时提醒逻辑（纯逻辑，不需要起服务）。"""
import os
import sys
import json
from datetime import datetime, timedelta

SERVER_DIR = r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\server\xiaozhi-server"
PROJECT = r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent"
os.chdir(SERVER_DIR)
sys.path.insert(0, SERVER_DIR)

from core import notify_plugin as np  # noqa: E402

print("=== A. 路径解析 ===")
print("  bridge_dir :", np._bridge_dir())
inbox, done, announced = np._dirs()
print("  inbox      :", inbox.exists(), "done:", done.exists(), "announced:", announced.exists())

print("\n=== B. 时间解析 ===")
for s in ["08:00", "23:59", (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M"), "乱写"]:
    t, is_clock = np._parse_at(s)
    print("  %-22s -> %s  clock=%s" % (s, t, is_clock))

print("\n=== C. 取件 watcher（造一个已完成任务） ===")
np._save_reminders([])  # 清空提醒文件
tid = "selftest-" + datetime.now().strftime("%H%M%S")
f = done / f"task-{tid}.md"
f.write_text(
    f"# 任务 {tid}\n\n- 任务编号：{tid}\n- 创建时间：x\n- 来源：语音（小智 deskemoji）\n- 状态：done\n\n"
    f"## 任务内容\n\n自检：把测试开关关掉\n\n## 执行结果\n\n已经关掉了，桌面也收拾干净了。\n",
    encoding="utf-8",
)
pend = np._pending_results()
print("  第一次 _pending_results:", [(t, ti, r[:30]) for t, ti, r in pend])
hit = [p for p in pend if p[0] == tid]
if hit:
    np._mark_announced(tid)
    pend2 = np._pending_results()
    print("  写标记后再查:", [t for t, _, _ in pend2], "-> 已消失:", tid not in [t for t, _, _ in pend2])
else:
    print("  !! 没找到自检任务")

print("\n=== D. 定时提醒（写一条 1 分钟后的） ===")
fire = (datetime.now() + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")
items = np._load_reminders()
rid = np._next_id(items)
items.append({"id": rid, "text": "自检提醒", "at": fire, "daily": False, "enabled": True, "next_at": fire})
np._save_reminders(items)
print("  写入后:", json.dumps(np._load_reminders(), ensure_ascii=False))
np._save_reminders([])
print("  已清空（自检不留垃圾）")

# 清理自检任务
try:
    f.unlink()
    (announced / f"{tid}.mark").unlink(missing_ok=True)
    print("  已清理自检任务文件")
except Exception as e:
    print("  清理失败:", e)

print("\nOK")
