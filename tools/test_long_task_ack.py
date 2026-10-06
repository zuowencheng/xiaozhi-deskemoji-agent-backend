# -*- coding: utf-8 -*-
"""
验证「长任务先打招呼」判定是否靠谱。

⚠️ 铁律 4d：用**真实数据**跑。这里直接读 bridge/done/ 里的 42 张真实任务单，
看分类是否符合直觉；再用 mock 验证 _ack_task 真的说了两种不同的话。
"""
import sys, os, re
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "server" / "xiaozhi-server"
sys.path.insert(0, str(SERVER))
os.chdir(SERVER)

from core import notify_plugin as npl  # noqa

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  -> " + str(extra)) if extra else ""))


print("\n=== ① 关键用例：不能把「打开个程序」也说成要花时间 ===")
CASES = [
    ("排查一下外接显示器为什么没画面", True, "排查型 → 该说要花时间"),
    ("查一下小智语音助手开场提示音由哪个配置项控制", True, "查一下 → 该说要花时间"),
    ("帮我整理一下桌面上的文件", True, "整理 → 该说要花时间"),
    ("在电脑上打开腾讯的 QQ 应用程序", False, "★ 原子操作，说要花时间会显得很傻"),
    ("打开微信", False, "★ 原子操作"),
    ("把桌面上的测试文件删掉", False, "原子操作"),
    ("显示桌面", False, "原子操作"),
]
for text, expect_long, why in CASES:
    got = npl._looks_long_task(text)
    ok = (got == expect_long)
    check(f"{'长任务' if got else '短任务'} <- {text[:24]}", ok, f"期望{'长' if expect_long else '短'} | {why}")

print("\n=== ② 真实任务单回扫（42 张历史单）===")
done = Path(__file__).resolve().parents[1] / "bridge" / "done"
files = sorted(done.glob("task-*.md"))
long_cnt = short_cnt = 0
long_samples = []
for p in files:
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        continue
    m = re.search(r"##\s*任务内容\s*\n+(.+?)(?:\n##|\Z)", txt, re.S)
    if not m:
        continue
    body = " ".join(m.group(1).split())
    if npl._looks_long_task(body):
        long_cnt += 1
        long_samples.append(body[:34])
    else:
        short_cnt += 1

print(f"   共 {long_cnt + short_cnt} 张，判定为长任务 {long_cnt} 张（{long_cnt * 100 // max(1, long_cnt + short_cnt)}%）")
print("   长任务样例：")
for s in long_samples[:6]:
    print("     ·", s)
check("长任务占比合理（不是全判长、也不是全判短）",
      0 < long_cnt < long_cnt + short_cnt, f"长 {long_cnt} / 短 {short_cnt}")

print("\n=== ③ _ack_task 真的说了两种不同的话吗（mock 播报）===")
spoken = []
orig_speak = npl.speak_or_queue
npl.speak_or_queue = lambda text, tag=None: spoken.append((tag, text))
npl._ACK_SWITCH[0] = True
try:
    npl._ack_task("# 任务 x\n\n- 状态：pending\n\n## 任务内容\n\n排查一下外接显示器为什么没画面\n\n## 执行结果\n\n")
    npl._ack_task("# 任务 y\n\n- 状态：pending\n\n## 任务内容\n\n在电脑上打开腾讯的 QQ 应用程序\n\n## 执行结果\n\n")
finally:
    npl.speak_or_queue = orig_speak

print("   实际播报：")
for tag, t in spoken:
    print(f"     [{tag}] {t}")
check("播了两条", len(spoken) == 2, len(spoken))
if len(spoken) == 2:
    long_txt = spoken[0][1]
    short_txt = spoken[1][1]
    check("长任务那句含「花点时间」", "花点时间" in long_txt, long_txt)
    check("长任务那句含「先忙别的」", "先忙别的" in long_txt)
    check("长任务那句含「弄完我叫你」", "弄完我叫你" in long_txt)
    check("短任务那句保持原样「正在处理」", "正在处理" in short_txt, short_txt)
    check("短任务那句没被误加「花点时间」", "花点时间" not in short_txt)

print("\n=== ④ 中途补话：任务已结束就不该再打扰 ===")
import asyncio  # noqa
npl._EXEC_RUNNING.clear()
spoken2 = []
npl.speak_or_queue = lambda text, tag=None: spoken2.append(text)
try:
    asyncio.run(npl._nudge_if_still_running(Path("bridge/inbox/task-none.md"), 0.05))
    check("任务已结束 → 不播（不打扰）", len(spoken2) == 0, spoken2)

    p = Path("bridge/inbox/task-running.md")
    npl._EXEC_RUNNING.add("task-running.md")
    asyncio.run(npl._nudge_if_still_running(p, 0.05))
    check("任务还在跑 → 播一句「还在弄」",
          len(spoken2) == 1 and "还在弄" in spoken2[0], spoken2)
finally:
    npl.speak_or_queue = orig_speak
    npl._EXEC_RUNNING.clear()

print("\n" + "=" * 60)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("❌ 失败项：")
    for f in FAIL:
        print("   -", f)
    sys.exit(1)
print("✅ 全部通过")
