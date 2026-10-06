# -*- coding: utf-8 -*-
"""
验证「免打扰时段」是否靠谱。

⚠️ 铁律 4d：静默类功能最容易**静默失效**——写错了不报错，只是"该播的没播"
或"半夜不该播的播了"。所以必须：
  ① 用真实模块跑（不复制逻辑）
  ② 覆盖跨午夜 / 边界 / 手滑配置
  ③ 验证 speak_or_queue 真的没调用 speak（不是只看返回值）
"""
import sys, os
from datetime import datetime
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "server" / "xiaozhi-server"
sys.path.insert(0, str(SERVER))
os.chdir(SERVER)

from core import notify_plugin as npl  # noqa

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  -> " + str(extra)) if extra else ""))


def at(h, m=0):
    return datetime(2026, 10, 4, h, m)


def with_cfg(cfg, fn):
    old = npl._notify_cfg
    npl._notify_cfg = lambda: cfg
    try:
        return fn()
    finally:
        npl._notify_cfg = old


print("\n=== ① 默认关闭：任何时间都不静默（最最重要，别一上来就把嘴堵了）===")
for h in (0, 1, 3, 12, 22, 23):
    got = with_cfg({"quiet_hours_enabled": False,
                    "quiet_hours_start": "23:00", "quiet_hours_end": "07:00"},
                   lambda h=h: npl._in_quiet_hours(at(h)))
    check(f"{h:02d}:00 不静默（开关关着）", got is False, got)

print("\n=== ② 23:00→07:00 跨午夜（默认配置）===")
CFG = {"quiet_hours_enabled": True, "quiet_hours_start": "23:00", "quiet_hours_end": "07:00"}
CASES = [
    (22, 59, False, "起点前一分钟，还能播"),
    (23, 0,  True,  "起点整点，开始静默"),
    (23, 30, True,  "深夜"),
    (0, 0,   True,  "零点，跨午夜关键用例"),
    (1, 0,   True,  "凌晨"),
    (6, 59,  True,  "终点前一分钟"),
    (7, 0,   False, "终点整点，恢复播报"),
    (12, 0,  False, "白天"),
]
for h, m, expect, why in CASES:
    got = with_cfg(CFG, lambda h=h, m=m: npl._in_quiet_hours(at(h, m)))
    check(f"{h:02d}:{m:02d} -> {'静默' if got else '播报'}", got is expect, f"期望{'静默' if expect else '播报'} | {why}")

print("\n=== ③ 不跨午夜 01:00→06:00（有人这么设）===")
CFG2 = {"quiet_hours_enabled": True, "quiet_hours_start": "01:00", "quiet_hours_end": "06:00"}
for h, m, expect, why in [(0, 30, False, "起点前"), (1, 0, True, "起点"),
                          (3, 0, True, "区间内"), (6, 0, False, "终点"),
                          (23, 0, False, "晚上该播")]:
    got = with_cfg(CFG2, lambda h=h, m=m: npl._in_quiet_hours(at(h, m)))
    check(f"{h:02d}:{m:02d} -> {'静默' if got else '播报'}", got is expect, why)

print("\n=== ④ 手滑把起止填成一样 → 视作无效，绝不变成全天静默 ===")
got = with_cfg({"quiet_hours_enabled": True, "quiet_hours_start": "23:00",
                "quiet_hours_end": "23:00"}, lambda: npl._in_quiet_hours(at(3, 0)))
check("起止相同 -> 不静默（防全天闭嘴）", got is False, got)

print("\n=== ⑤ YAML 六十进制坑：裸写 23:00 会被读成 int 1380 ===")
check("'23:00' 字符串 -> 1380", npl._hhmm_to_min("23:00", 0) == 23 * 60)
check("1380 整数 -> 1380（兜住无引号写法）", npl._hhmm_to_min(1380, 0) == 23 * 60)
check("420 整数 -> 420", npl._hhmm_to_min(420, 0) == 7 * 60)
check("垃圾值 -> 用默认值不崩", npl._hhmm_to_min("abc", 23 * 60) == 23 * 60)

print("\n=== ⑥ speak_or_queue：静默期真的没调 speak，且消息入了队 ===")
# 真实配置读一遍，确认 notify 段解析正确
real = npl._notify_cfg()
check("配置里 notify 段能读到", isinstance(real, dict), real)
check("默认开关是关的", bool(real.get("quiet_hours_enabled")) is False,
      real.get("quiet_hours_enabled"))
check("默认起点 23:00", str(real.get("quiet_hours_start")) == "23:00", real.get("quiet_hours_start"))
check("默认终点 07:00", str(real.get("quiet_hours_end")) == "07:00", real.get("quiet_hours_end"))

# mock：静默开启 + 当前凌晨 1 点
npl._notify_cfg = lambda: {"quiet_hours_enabled": True,
                           "quiet_hours_start": "23:00", "quiet_hours_end": "07:00"}
called = {"speak": 0}
old_speak, old_enqueue = npl.speak, npl.enqueue
# 真实 speak() 返回 (ok, info) 元组，mock 必须同构
def _mk_counter(c):
    def _speak(t):
        c["speak"] += 1
        return True, "mocked"
    return _speak


npl.speak = _mk_counter(called)
npl.enqueue = lambda t, tag="": 7
try:
    ok, info = npl.speak_or_queue("测试免打扰", tag="test")
finally:
    npl.speak, npl.enqueue = old_speak, old_enqueue
    npl._notify_cfg = lambda: real

check("静默期 speak 一次都没被调用", called["speak"] == 0, f"speak 被调用 {called['speak']} 次")
check("返回 (False, quiet(...))", ok is False and "quiet" in str(info), (ok, info))

print("\n=== ⑦ 非静默期：照常播（开关关着时行为与改动前完全一致）===")
npl._notify_cfg = lambda: {"quiet_hours_enabled": False}
called2 = {"speak": 0}
npl.speak = _mk_counter(called2)
try:
    ok2, _ = npl.speak_or_queue("正常播报", tag="test")
finally:
    npl.speak = old_speak
    npl._notify_cfg = lambda: real
check("非静默期 speak 被调 1 次", called2["speak"] == 1, called2["speak"])
check("非静默期返回 ok=True", ok2 is True, ok2)

print("\n" + "=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    for f in FAIL:
        print("  FAIL:", f)
    sys.exit(1)
print("全部通过")
