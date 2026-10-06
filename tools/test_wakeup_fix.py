# -*- coding: utf-8 -*-
"""验证 2026-10-03 23:40 那次翻车的两处修复

翻车回放（真实日志）：
    23:40:34 唤醒 → 播「还没睡？」
    23:40:38 ASR definite 帧只给「你好，小智。」→ 守卫静默丢弃
             后面 2580/2940 帧明明还有「还没呢你」，因连接关闭蒸发
    23:40:59 主人忍无可忍：「为什么不回答我？」→ LLM 脑补出「拜拜，晚安」

本脚本验证：
    1. doubao_stream._collect_remainder 能把被切掉的后半句补回来
    2. greeting_guard 不再一律静默丢弃（drop / prompt 两级）
"""
import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRV = os.path.join(ROOT, "server", "xiaozhi-server")
sys.path.insert(0, SRV)
os.chdir(SRV)


class _L:
    def bind(self, **k):
        return self

    def warning(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def debug(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


# ---------------------------------------------------------------- 1. 续接收集
class FakeWS:
    def __init__(self, frames):
        self.q = list(frames)

    async def recv(self):
        if not self.q:
            await asyncio.sleep(30)  # 没有帧了 → 让 wait_for 超时
        return self.q.pop(0)


class FakeStop:
    def is_set(self):
        return False


def frame(text, duration=0, last=False):
    r = {"text": text}
    if last:
        r["last_stream_duration"] = duration
    return {"payload_msg": {"result": r, "audio_info": {"duration": duration}}}


def run_collect(first_text, frames, window=0.3):
    from core.providers.asr.doubao_stream import ASRProvider

    prov = object.__new__(ASRProvider)
    prov.asr_ws = FakeWS(frames)
    prov.parse_response = lambda r: r
    conn = type("C", (), {"stop_event": FakeStop(), "asr_audio": []})()
    return asyncio.run(prov._collect_remainder(conn, first_text, window))


def test_collect():
    print("== 1. _collect_remainder（补齐被 definite 切掉的后半句） ==")
    ok = True

    # ① 23:40:38 那次的真实序列：definite 给「你好，小智。」，后面还有「还没呢你」
    got = run_collect(
        "你好，小智。",
        [
            frame("你好，小智。还没", 2580),
            frame("你好，小智。还没呢你", 2940, last=True),
        ],
    )
    want = "你好，小智。还没呢你"
    print(f"  ① 真实序列补齐   : {got!r}  期望 {want!r}  {'OK' if got == want else 'FAIL'}")
    ok &= got == want

    # ② 没有后续帧 → 原样返回，不能卡住
    t0 = time.monotonic()
    got = run_collect("你好，小智。", [], window=0.3)
    dt = time.monotonic() - t0
    print(f"  ② 无后续超时退回 : {got!r}  耗时 {dt:.2f}s  {'OK' if got == '你好，小智。' and dt < 2 else 'FAIL'}")
    ok &= got == "你好，小智。" and dt < 2

    # ③ 新帧带 last_stream_duration → 立刻收，不等满窗口
    t0 = time.monotonic()
    got = run_collect("你好。", [frame("你好。帮我查油价", 3000, last=True)], window=5)
    dt = time.monotonic() - t0
    print(f"  ③ 遇 last 提前收 : {got!r}  耗时 {dt:.2f}s  {'OK' if got == '你好。帮我查油价' and dt < 2 else 'FAIL'}")
    ok &= got == "你好。帮我查油价" and dt < 2

    # ④ 续接帧比已识别的短 → 不回退（取最长）
    got = run_collect("你好，小智。还没呢你", [frame("你好", 3000)])
    print(f"  ④ 短帧不覆盖长句 : {got!r}  {'OK' if got == '你好，小智。还没呢你' else 'FAIL'}")
    ok &= got == "你好，小智。还没呢你"

    return ok


# ---------------------------------------------------------------- 2. 守卫三态
def test_guard():
    print("\n== 2. greeting_guard（不再一律静默丢弃） ==")
    from core.handle.greeting_guard import is_dup_wakeup_misrecognition

    def conn(session, gap):
        c = type("C", (), {})()
        c.config = {"wakeup_words": ["你好小智"], "greeting_debounce": 600}
        c.greeting_session_id = session
        c.greeting_played_at = time.time() - gap
        c.logger = _L()
        return c

    cases = [
        ("没播过问候 → 放行", conn(None, 0), "你好小智", ""),
        ("1.5s 内纯唤醒词 → drop", conn("S1", 0.8), "你好小智", "drop"),
        ("过静默窗纯唤醒词 → prompt", conn("S1", 3.0), "你好小智", "prompt"),
        ("含别的字 → 放行", conn("S1", 3.0), "你好小智帮我查油价", ""),
        ("超时 600s → 放行", conn("S1", 700), "你好小智", ""),
        ("空文本 → 放行", conn("S1", 3.0), "", ""),
    ]
    ok = True
    for name, c, text, want in cases:
        got = is_dup_wakeup_misrecognition(c, text)
        flag = "OK" if got == want else "FAIL"
        print(f"  {name:28s}: {got!r:10s} 期望 {want!r:10s} {flag}")
        ok &= got == want

    # 兼容性：旧代码 `if is_dup(...)` 仍能当布尔用
    truthy = bool(is_dup_wakeup_misrecognition(conn("S1", 3.0), "你好小智"))
    falsy = bool(is_dup_wakeup_misrecognition(conn("S1", 3.0), "今天天气"))
    print(f"  布尔兼容（命中=True/放行=False）: {truthy}/{falsy}  {'OK' if truthy and not falsy else 'FAIL'}")
    ok &= truthy and not falsy
    return ok


if __name__ == "__main__":
    r1 = test_collect()
    r2 = test_guard()
    print("\n结论:", "全部通过 ✅" if (r1 and r2) else "有失败 ❌")
    sys.exit(0 if (r1 and r2) else 1)
