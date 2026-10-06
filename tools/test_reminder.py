# -*- coding: utf-8 -*-
"""验证 reminder 插件：注册、设提醒、列提醒、取消。"""
import os
import sys

SERVER_DIR = r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\server\xiaozhi-server"
os.chdir(SERVER_DIR)
sys.path.insert(0, SERVER_DIR)

from plugins_func.register import all_function_registry, module_func_map  # noqa: E402
import plugins_func.functions.reminder as rm  # noqa: E402
from core import notify_plugin as np  # noqa: E402

print("[1] reminder 展开:", module_func_map.get("reminder"))
print("[2] pc_control 展开:", module_func_map.get("pc_control"))
for n in ("set_reminder", "list_reminders", "cancel_reminder"):
    print("    %-16s in registry: %s" % (n, n in all_function_registry))

np._save_reminders([])
r1 = rm.set_reminder(text="该交房租了", at="2026-10-03T08:00")
print("\n[3] set_reminder ->", r1.action, "|", str(r1.result)[:110])
r2 = rm.set_reminder(text="起来活动一下", at="09:00", daily=True)
print("[4] set_reminder(每天) ->", str(r2.result)[:110])
r3 = rm.set_reminder(text="坏时间", at="乱写")
print("[5] 非法时间 ->", str(r3.result)[:80])

added = np._load_reminders()
print("\n[6] 当前提醒:", [(i["id"], i["text"], i["at"], i["daily"]) for i in added])

print("\n[7] list_reminders ->", str(rm.list_reminders().result)[:160])
print("\n[8] cancel r1 ->", str(rm.cancel_reminder("r1").result)[:80])
print("[9] cancel r99 ->", str(rm.cancel_reminder("r99").result)[:80])
print("[10] 剩余:", [i["id"] for i in np._load_reminders()])

np._save_reminders([])
print("\n已清空自检数据。OK")
