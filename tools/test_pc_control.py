# -*- coding: utf-8 -*-
"""验证 pc_control 插件：能加载、能展开成两个工具、能真的把任务写进投递箱。"""
import os
import sys
import glob

SERVER_DIR = r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\server\xiaozhi-server"
PROJECT = r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent"
os.chdir(SERVER_DIR)
sys.path.insert(0, SERVER_DIR)

from plugins_func.register import all_function_registry, module_func_map  # noqa: E402
import plugins_func.functions.pc_control as pc  # noqa: E402

print("[1] module_func_map['pc_control'] =", module_func_map.get("pc_control"))
print("[2] pc_run_task   in registry:", "pc_run_task" in all_function_registry)
print("[3] pc_query_task in registry:", "pc_query_task" in all_function_registry)
print("[4] 计算出的投递箱目录:", pc._bridge_dir())

before = set(glob.glob(os.path.join(PROJECT, "bridge", "inbox", "task-*.md")))
resp = pc.pc_run_task(task="[自检] 这是一条由 pc_control 插件自检写入的任务，请忽略")
print("[5] pc_run_task 返回 action:", resp.action, "| result:", str(resp.result)[:120])
after = set(glob.glob(os.path.join(PROJECT, "bridge", "inbox", "task-*.md")))
new = sorted(after - before)
print("[6] 新增文件:", new)

if new:
    with open(new[0], "r", encoding="utf-8") as f:
        print("[7] 文件内容 ----")
        print(f.read())
    print("[8] 清理自检文件:", os.path.basename(new[0]))
    os.remove(new[0])

q = pc.pc_query_task()
print("[9] pc_query_task ->", q.action, "|", str(q.result)[:160])
print("\nOK: 插件可用" if new else "\nFAIL: 没写出文件")
