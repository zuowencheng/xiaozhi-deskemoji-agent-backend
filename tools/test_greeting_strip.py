# -*- coding: utf-8 -*-
"""
greeting_guard.strip_wakeup_prefix 单测（2026-10-13）

为什么必须测：这条闸是"静默类防护"—— 写错了日志上表现为
「该拦的没拦」和「逻辑根本没跑」长得一模一样，只能靠单测证伪。

用例全部取自真实日志：
    20:53:51 识别文本: 你好，小智。你现在      → 剥完只剩「你现在」，应判残片
    20:55:00 识别文本: 你好，小智。打开酷狗音乐 → 剥完是「打开酷狗音乐」，必须照常执行
    20:55:16 识别文本: 播放周杰伦的晴天        → 无唤醒词前缀，原样放行
"""
import importlib.util
import os
import time

GUARD = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "server", "xiaozhi-server", "core", "handle", "greeting_guard.py",
)

_spec = importlib.util.spec_from_file_location("greeting_guard", os.path.abspath(GUARD))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

WAKE_WORDS = [
    "你好小智", "嘿你好呀", "你好小志", "小爱同学",
    "你好小鑫", "你好小新", "小美同学", "小龙小龙",
]


class _Log:
    def bind(self, **kw):
        return self

    def warning(self, m):
        pass

    def info(self, m):
        pass


class _Conn:
    """greeting_played_at 往前推 gap 秒，模拟'问候已播出 gap 秒'。"""

    def __init__(self, gap, session="sess-1"):
        self.config = {
            "wakeup_words": WAKE_WORDS,
            "greeting_debounce": 600.0,
        }
        self.logger = _Log()
        self.greeting_session_id = session
        self.greeting_played_at = time.time() - gap


# (输入原文, 距问候播出秒数, 期望 action, 期望剥离结果, 本轮是否播过问候)
CASES = [
    # —— 核心场景：老闸放行的「唤醒词 + 半截话」，现在必须拦住 ——
    ("你好，小智。你现在",        3.0, "prompt", None, True),
    ("你好，小智。你好",          3.0, "prompt", None, True),
    ("你好，小智。",              3.0, "prompt", None, True),
    ("你好，小智。",              0.5, "drop",   None, True),  # 刚播完，唤醒词音频残留

    # —— 真问题：剥掉唤醒词后必须照样执行，绝不丢主人的话 ——
    ("你好，小智。打开酷狗音乐",   3.0, "strip", "打开酷狗音乐", True),
    ("你好小智你现在使用什么大脑", 3.0, "strip", "你现在使用什么大脑", True),
    ("你好，小智。帮我查今天北京天气", 3.0, "strip", "帮我查今天北京天气", True),

    # —— 无关输入：原样放行 ——
    ("播放周杰伦的晴天",          3.0, "",      "播放周杰伦的晴天", True),
    ("打开灯",                    3.0, "",      "打开灯", True),

    # —— 边界：本轮没播过问候 / 超时 → 一律放行 ——
    ("你好，小智。你现在",        3.0, "",      "你好，小智。你现在", False),
    ("你好，小智。你现在",      700.0, "",      "你好，小智。你现在", True),
]

passed = failed = 0
print(f"{'输入':<28} {'gap':>6}  {'期望':<7} {'实际':<7} {'剥离结果'}")
print("-" * 78)

for text, gap, want_act, want_out, greeted in CASES:
    conn = _Conn(gap, session="sess-1" if greeted else None)
    act, out = _mod.strip_wakeup_prefix(conn, text)
    ok = (act == want_act) and (want_out is None or out == want_out)
    mark = "OK " if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"{text:<28} {gap:>6.1f}  {want_act:<7} {act:<7} {out!r:<20} {mark}")

print("-" * 78)
print(f"通过 {passed} / {len(CASES)}，失败 {failed}")
raise SystemExit(1 if failed else 0)
