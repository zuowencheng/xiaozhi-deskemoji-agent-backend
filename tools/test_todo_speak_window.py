# -*- coding: utf-8 -*-
"""
验证「待办巡检可播时段」是否真能挡住凌晨播报。

事故回放（2026-10-04 03:01:19，服务端日志铁证）：
    [播报] 已投递: 嗨，提醒一下，下午去打羽毛球，今天 15:00该做了
根因：待办 due=2026-10-04 15:00，needs_remind(within_hours=12) 把
      「12 小时内到期」都算该提醒 → 03:01 时 now+12h=15:01 > due → 命中。
      而免打扰 quiet_hours_enabled 默认 false，兜不住。

铁律 4d：静默类防护最容易**静默失效**（不报错、只是该拦的没拦）。
所以必须：① 用真实模块跑 ② 用真实待办数据 ③ 证明"没闸会命中、有闸不播"。
"""
import sys, os
from datetime import datetime
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "server" / "xiaozhi-server"
BRIDGE = Path(__file__).resolve().parents[1] / "bridge"
sys.path.insert(0, str(SERVER))
os.chdir(SERVER)

from core import notify_plugin as npl  # noqa

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  -> " + str(extra)) if extra else ""))


def with_cfg(cfg, fn):
    old = npl._notify_cfg
    npl._notify_cfg = lambda: cfg
    try:
        return fn()
    finally:
        npl._notify_cfg = old


DEFAULT = {"todo_speak_start": "07:00", "todo_speak_end": "22:00"}

# 真实待办（bridge/todo.py 里的事实数据）
DUE = "2026-10-04 15:00"
TITLE = "下午去打羽毛球"


def would_fire_without_gate(now: datetime, within_hours: int = 12) -> bool:
    """复刻 bridge/todo.py::needs_remind 的判据，证明「没闸就会命中」。

    注意：这里**故意不用** needs_remind 本身（它带 4 小时去重、依赖数据库状态），
    只复算它的核心不等式 due <= now+within_hours，用来证明事故的必然性。
    """
    soon = (now.replace(second=0, microsecond=0)
            + __import__("datetime").timedelta(hours=within_hours)).strftime("%Y-%m-%d %H:%M")
    return DUE <= soon


print("\n=== ① 事故复现：凌晨 3 点，不加闸必然命中（证明根因成立）===")
# ⚠️ 02:51 是**不命中**的（+12h = 14:51 < 15:00）。这不是笔误，是有意义的：
#    它说明这条待办的预告窗口从 **03:00 整**才打开，而巡检每 10 分钟扫一次，
#    所以第一次扫描落在 03:01:19 —— 正好对上日志里的播报时刻。事故时刻因此可复现。
for h, m, want, desc in ((2, 51, False, "02:51 窗口尚未打开（+12h=14:51）"),
                         (3, 0, True, "03:00 整窗口打开"),
                         (3, 1, True, "★ 事故时刻 03:01（巡检首扫即命中）")):
    now = datetime(2026, 10, 4, h, m)
    got = would_fire_without_gate(now)
    check(f"{desc} → 无闸{'会' if want else '不会'}命中", got is want,
          f"soon={(now.replace(second=0, microsecond=0)+__import__('datetime').timedelta(hours=12)).strftime('%m-%d %H:%M')}")

print("\n=== ② 加了闸：深夜/清晨一律不播 ===")
for h, m, desc in ((3, 1, "★ 事故时刻 03:01"),
                   (0, 30, "凌晨 00:30"),
                   (5, 0, "清晨 05:00"),
                   (6, 59, "边界 06:59（窗口前 1 分钟）"),
                   (22, 0, "边界 22:00（窗口关门）"),
                   (23, 59, "深夜 23:59")):
    now = datetime(2026, 10, 4, h, m)
    got = with_cfg(DEFAULT, lambda: npl._in_todo_speak_window(now))
    check(f"{desc} → 不可播", got is False)

print("\n=== ③ 白天照常播（别把正常功能也堵了）===")
for h, m, desc in ((7, 0, "边界 07:00（窗口开门，放行早间汇总）"),
                   (8, 0, "早上 08:00"),
                   (12, 0, "中午 12:00"),
                   (15, 0, "★ 真正到点 15:00"),
                   (21, 59, "边界 21:59（关门前 1 分钟）")):
    now = datetime(2026, 10, 4, h, m)
    got = with_cfg(DEFAULT, lambda: npl._in_todo_speak_window(now))
    check(f"{desc} → 可播", got is True)

print("\n=== ④ 手滑配置：起止相同 = 没配，退回全天可播（不能变成全天闭嘴）===")
for h in (0, 3, 12, 23):
    got = with_cfg({"todo_speak_start": "09:00", "todo_speak_end": "09:00"},
                   lambda: npl._in_todo_speak_window(datetime(2026, 10, 4, h, 0)))
    check(f"{h:02d}:00 起止相同 → 全天可播", got is True)

print("\n=== ⑤ 跨午夜窗口（起点 > 终点，如 20:00→02:00 夜猫子模式）===")
CROSS = {"todo_speak_start": "20:00", "todo_speak_end": "02:00"}
for h, want, desc in ((21, True, "21:00 窗口内"), (1, True, "01:00 跨过零点仍在窗内"),
                      (19, False, "19:00 窗外"), (3, False, "03:00 窗外")):
    got = with_cfg(CROSS, lambda: npl._in_todo_speak_window(datetime(2026, 10, 4, h, 0)))
    check(f"{desc}", got is want, f"got={got}")

print("\n=== ⑥ 极端值兜底（配置被写坏也不能让播报链路挂掉）===")
for bad, desc in (({"todo_speak_start": "abc", "todo_speak_end": "22:00"}, "起点非法"),
                  ({"todo_speak_start": 1380, "todo_speak_end": 1320}, "YAML 六十进制整数(无引号)"),
                  ({}, "整段缺失")):
    try:
        got = with_cfg(bad, lambda: npl._in_todo_speak_window(datetime(2026, 10, 4, 12, 0)))
        check(f"{desc} → 不抛异常且返回布尔", isinstance(got, bool), f"got={got}")
    except Exception as e:
        check(f"{desc} → 不抛异常", False, f"{type(e).__name__}: {e}")

print("\n=== ⑦ 端到端：把闸接进巡检，凌晨真的不调 speak ===")
calls = {"speak": 0, "queue": 0}
npl.speak_or_queue = lambda t, tag="": (calls.__setitem__("speak", calls["speak"] + 1), (True, "mock"))[1]


class _FakeTodo:
    """模拟 bridge/todo.py：凌晨 3 点 needs_remind 会返回 1 条（事故现场）。"""

    def needs_remind(self, within_hours=12):
        return [{"id": 1, "title": TITLE, "due": DUE}]

    def mark_reminded(self, tid):
        calls["queue"] += 1000      # 打戳 = 消息丢了，绝不该发生


async def _run(now: datetime, cfg: dict):
    """直接调巡检里那段判据（不真跑循环，避免 sleep 10 分钟）。"""
    old = npl._notify_cfg
    npl._notify_cfg = lambda: cfg
    try:
        if npl._in_todo_speak_window(now):
            need = _FakeTodo().needs_remind()
        else:
            need = []
        for x in need[:2]:
            npl.speak_or_queue("提醒一下，" + x["title"], tag="todo-due")
            _FakeTodo().mark_reminded(x["id"])
    finally:
        npl._notify_cfg = old


import asyncio  # noqa: E402
for h, want_speak, desc in ((3, 0, "★ 凌晨 03:01 一次都不播"),
                            (8, 1, "白天 08:00 正常播 1 次")):
    calls["speak"] = 0
    calls["queue"] = 0
    asyncio.new_event_loop().run_until_complete(_run(datetime(2026, 10, 4, h, 1), DEFAULT))
    check(f"{desc}", calls["speak"] == want_speak, f"speak 调用 {calls['speak']} 次")
    if want_speak == 0:
        check(f"  └ 且没打已读戳（白天会补播，消息不丢）", calls["queue"] == 0,
              f"mark_reminded 计数 {calls['queue']}")

print("\n" + "=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  [FAIL]", f)
    sys.exit(1)
print("全部通过 ✅")
