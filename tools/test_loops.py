# -*- coding: utf-8 -*-
"""真跑一遍两个循环：取件 watcher + 定时提醒（注入假连接，不需要真设备）。"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

SERVER_DIR = r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\server\xiaozhi-server"
os.chdir(SERVER_DIR)
sys.path.insert(0, SERVER_DIR)

from core import notify_plugin as np  # noqa: E402

SINK = []


class FakeTTS:
    def tts_one_sentence(self, conn, ctype, content_detail=""):
        SINK.append(content_detail)


class FakeConn:
    def __init__(self):
        self.tts = FakeTTS()


async def main():
    np._save_reminders([])
    _, done, ann = np._dirs()

    tid = "loop-" + datetime.now().strftime("%H%M%S")
    f = done / f"task-{tid}.md"
    f.write_text(
        f"# 任务 {tid}\n\n- 任务编号：{tid}\n- 状态：done\n\n"
        f"## 任务内容\n\n自检：整理桌面文件\n\n## 执行结果\n\n桌面已经整理好了，一共归了 3 个文件夹。\n",
        encoding="utf-8",
    )

    items = np._load_reminders()
    items.append({
        "id": "rT", "text": "自检提醒：该休息了", "at": (datetime.now()).isoformat(timespec="seconds"),
        "daily": False, "enabled": True,
        "next_at": (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds"),
    })
    np._save_reminders(items)

    # 注入假设备（等价于设备已连上 WS）
    np.REGISTRY["fake-device"] = FakeConn()
    print("[注入] REGISTRY =", list(np.REGISTRY.keys()))

    t1 = asyncio.create_task(np._watch_bridge({"poll_interval": 1}))
    t2 = asyncio.create_task(np._reminder_loop({}))
    await asyncio.sleep(14)
    t1.cancel()
    t2.cancel()
    await asyncio.sleep(0.3)

    print("\n[播报内容] 共 %d 条：" % len(SINK))
    for s in SINK:
        print("   ->", s[:120])

    mark = ann / f"{tid}.mark"
    print("\n[标记] announced 已写:", mark.exists())
    left = np._load_reminders()
    print("[提醒] 剩余:", [(i["id"], i.get("enabled")) for i in left])

    # 清理
    f.unlink(missing_ok=True)
    mark.unlink(missing_ok=True)
    np._save_reminders([])
    print("\n已清理。OK" if SINK else "\nFAIL: 没有任何播报")


asyncio.run(main())
