# -*- coding: utf-8 -*-
"""
端到端测一遍：派活 -> 执行器干 -> 落 done -> 看板刷新 -> 主动播报
顺带验证 2026-10-03 新增的「工作空间」字段（主人说"在某某项目里…"）。
"""
import sys
import time
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "server" / "xiaozhi-server"
sys.path.insert(0, str(SERVER))

from core.pc_agent import _resolve_workspace  # noqa: E402
from plugins_func.functions.pc_control import _dirs, _task_body, _new_id  # noqa: E402

print("=== 1. 工作空间解析 ===")
for name in ["2026-10-02-01-36-40", "C:\\Users\\zuowe\\Desktop", "不存在的空间xyz"]:
    print(f"   {name!r:35s} -> {_resolve_workspace(name) or '(空，交给模型自己找)'}")

print()
print("=== 2. 派一张带空间的活 ===")
inbox, done, _ = _dirs()
tid = _new_id()
task = (
    "在 C:\\Users\\zuowe\\WorkBuddy\\2026-10-02-01-36-40 目录下新建一个 txt 文件，"
    "文件名 xiaozhi-ws-test.txt，内容写：小智派活链路测试通过。写完告诉我文件路径。"
)
f = inbox / f"task-{tid}.md"
f.write_text(_task_body(tid, task, workspace="2026-10-02-01-36-40"), encoding="utf-8")
print(f"   已投递: {f.name}  (workspace=2026-10-02-01-36-40)")

print()
print("=== 3. 等执行器跑完（最多 90s）===")
target = done / f.name
for i in range(90):
    if target.exists():
        break
    time.sleep(1)
if target.exists():
    t = target.read_text(encoding="utf-8", errors="replace")
    print("   状态:", [l for l in t.splitlines() if "状态" in l][:1])
    print("   结果:", t.split("## 执行结果")[-1].strip()[:300])
else:
    print("   超时未完成，检查后台日志")

print()
print("=== 4. 看板是否刷新 ===")
board = Path(__file__).resolve().parents[1] / "小智任务中心.md"
if board.exists():
    head = [l for l in board.read_text(encoding="utf-8").splitlines() if l.startswith(">")][:1]
    print("   ", head[0] if head else "(空)")
    print("   含本任务:", tid in board.read_text(encoding="utf-8"))
else:
    print("   看板不存在")
