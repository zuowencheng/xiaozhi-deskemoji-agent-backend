"""
补播队列去重自测（2026-10-13 修「补播风暴」后新增）

背景：离线 5 天积压 59 条待办/额度提醒，上线后 2 分钟全刷完，
      把 TTS 免费额度（20000 字符）烧到只剩 545。
      根因是 enqueue() 完全没有去重，而待办巡检每 10 分钟就重新入队一条
      （窗口外不打 reminded_at 戳，每次都判定"需要提醒"）。

本测试不启动服务、不碰真实队列文件：import 模块后把 _queue 和 _queue_save
换成内存对象，只验证 enqueue() 的去重行为。

用法（必须用服务端 venv，模块依赖 loguru 等）：
  C:\\Users\\zuowe\\.workbuddy\\binaries\\python\\envs\\xiaozhi-server\\Scripts\\python.exe tools/test_enqueue_dedup.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRV = ROOT / "server" / "xiaozhi-server"
for p in (SRV, SRV / "core"):
    sys.path.insert(0, str(p))

import notify_plugin as np  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def reset():
    """把队列换成干净的内存列表，_queue_save 变成 no-op（绝不碰真实文件）。"""
    np._queue.clear()
    np._queue_save = lambda: None


def texts():
    return [i["text"] for i in np._queue]


print("=" * 68)
print("补播队列去重自测")
print("=" * 68)

# ---- 1. 同一条待办、天数在变 → 只留最新一条 -------------------------------
reset()
np.enqueue("嗨，提醒一下，给手机换新膜，已经过期 3 天该做了", "todo-due")
np.enqueue("嗨，提醒一下，给手机换新膜，已经过期 4 天该做了", "todo-due")
np.enqueue("嗨，提醒一下，给手机换新膜，已经过期 8 天该做了", "todo-due")
check("同待办天数变化只留 1 条", len(np._queue) == 1, f"实际 {len(np._queue)} 条")
check("保留的是最新那条（过期 8 天）",
      "过期 8 天" in texts()[0], texts()[0][:40] if np._queue else "空")

# ---- 2. 同 tag 但不同待办 → 不能误伤 --------------------------------------
reset()
np.enqueue("嗨，提醒一下，给手机换新膜，已经过期 3 天该做了", "todo-due")
np.enqueue("嗨，提醒一下，在 Soul 发布羽毛球场地测评，已经过期 3 天该做了", "todo-due")
check("两条不同待办都保留", len(np._queue) == 2, f"实际 {len(np._queue)} 条")

# ---- 3. 不同 tag → 互不影响 ----------------------------------------------
reset()
np.enqueue("嗨，提醒一下，语音合成2.0的免费额度只剩1591字符了", "quota-low")
np.enqueue("任务已完成：整理桌面", "task-done")
np.enqueue("嗨，提醒一下，给手机换新膜，已经过期 3 天该做了", "todo-due")
check("不同 tag 各留一条", len(np._queue) == 3, f"实际 {len(np._queue)} 条")

# ---- 4. 空文本不入队 ------------------------------------------------------
reset()
n0 = np.enqueue("   ", "todo-due")
check("空白文本不入队", len(np._queue) == 0 and n0 == 0)

# ---- 5. 队列超 30 条时丢最旧（兜底，防止去重失效时烧穿额度）--------------
reset()
for i in range(35):
    np.enqueue(f"第{i:02d}条唯一消息，前缀各不相同", f"tag{i}")
check("超过 30 条被截到 30", len(np._queue) == 30, f"实际 {len(np._queue)} 条")
check("丢的是最旧的第00条", "第00条" not in " ".join(texts()))
check("最新一条还在", any("第34条" in t for t in texts()))

# ---- 6. 带前缀的同源消息（active_greeting 加上"嗨，"后）仍能命中 ----------
reset()
np.enqueue("嗨，提醒一下，给手机换新膜，已经过期 3 天该做了", "todo-due")
np.enqueue("嗨，提醒一下，给手机换新膜，已经过期 9 天该做了", "todo-due")
check("前缀一致时仍去重", len(np._queue) == 1, f"实际 {len(np._queue)} 条")

print()
print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项：", FAIL)
print("=" * 68)
sys.exit(1 if FAIL else 0)
