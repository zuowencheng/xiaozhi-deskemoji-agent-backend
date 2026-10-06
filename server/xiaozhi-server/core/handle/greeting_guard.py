"""唤醒词去重守卫（2026-10-03 23:35 集中提取）

为什么要单独一个模块
-------------------
这个问题我**连修两次都修错了层**：

- 第 1 次（22:25）：以为"问候语误走 startToChat"，修在 `listenMessageHandler` 的 detect 分支
- 第 2 次（23:15）：以为"去重闸没覆盖"，还是修在 detect 分支
- 第 3 次（23:35）：查日志才发现 —— **主人说的话根本不走 detect 分支**

关键证据（23:24 那次的日志）：
```
23:24:05  listen detect "你好小智"  → 播「还没睡？」       ← 走 detect
23:24:12  识别文本: 你好，小智。      → 直接进大模型         ← 没对应的 listen 消息！
23:24:13  语音生成成功: 我在呢
23:24:14  语音生成成功: 怎么了？
```
`识别文本:` 来自 `core/providers/asr/base.py:150`，那一行之后走
`base.py:170 → startToChat(conn, enhanced_text)`，**完全绕过 detect 分支**。
所以写在 detect 里的闸，一次都不会被触发。

教训：**改"某类输入不触发某行为"时，必须先确认这条输入真实的路径。**
我是靠"日志里有 `识别文本` 却没有 `listen消息`"这个不对称才定位到的 ——
**日志里的不对称就是破案线索。**

守卫放在真正入口（`asr/base.py`）后，detect 分支里那份就变成冗余兜底。
两边都留着是对的：万一以后 ASR 改回走 detect，闸也还在。

判据
----
本轮已播过唤醒问候（`conn.greeting_session_id` 有值）**且** 本条识别文本
**清洗后恰好等于某个唤醒词** → 判为 ASR 误识别。

⛔ 关键：**只丢纯唤醒词**。
"你好帮我查下油价" 这类含别的字的内容必须照常送 LLM，
否则会丢主人的真问题 —— 这是本模块存在的唯一意义，别写错。

⭐ 2026-10-03 23:50 补：命中了也不能一律"静默丢弃"
--------------------------------------------------
23:40 那次翻车就是这么来的：主人回了一句「还没呢你」，
ASR 因为 definite 提前断句只吐出「你好，小智。」（前半截），
守卫一看"这不就是唤醒词吗"→ 静默丢弃 → **主人干等 18 秒**，
屏幕上一直"聆听中"，直到主人忍无可忍问「为什么不回答我？」。

教训：**静默丢弃的代价比多说一个字大得多。**
所以命中后按"距问候播出多久"分两级：

  - 1.5 秒内  → "drop"：主人大概率还没开口，这是唤醒词音频的残留，静默丢掉
  - 超过 1.5 秒 → "prompt"：主人明明开口了却只听清唤醒词，说明**没听清**，
                  回一声「嗯？」提示重说，绝不让主人对着空气等

（配合 `doubao_stream._collect_remainder` 补齐后半句后，
绝大多数"话被截一半"的情况根本到不了守卫这一层。）
"""
from __future__ import annotations

TAG = "GreetingGuard"

# 刚播完问候的这么久以内，纯唤醒词视为"唤醒词音频的残留"，静默丢
SILENT_WINDOW = 1.5


def is_dup_wakeup_misrecognition(conn, filtered_text: str) -> str:
    """判断这条输入是不是「同轮里被 ASR 二次识别出来的唤醒词」。

    参数:
        conn:           ConnectionHandler
        filtered_text:  已去标点的识别文本
    返回:
        ""       → 照常处理（主人的真输入）
        "drop"   → 静默丢弃（唤醒词残留，主人还没开口）
        "prompt" → 只听清唤醒词但已过静默窗 → 回一声提示，别让主人干等

    ⚠️ 返回值是字符串不是 bool：非空 = "是唤醒词"（旧代码 `if is_dup(...)` 仍成立），
       但调用方应区分 drop / prompt 两种处理。异常一律返回 ""（放行）。
    """
    try:
        # ① 本轮是否播过问候。没播过 → 这次是真唤醒，不拦。
        if not getattr(conn, "greeting_session_id", None):
            return ""

        # ② 双重保险：超时则放行。设备异常时会话不换，
        #    但主人可能隔很久才说下一句 —— 那时是真需求，不该拦。
        import time
        gap_cfg = float(conn.config.get("greeting_debounce", 600.0))
        gap = time.time() - float(getattr(conn, "greeting_played_at", 0.0))
        if gap > gap_cfg:
            return ""

        # ③ ⭐ 核心判据：内容必须**恰好等于**某个唤醒词。
        #    等于 = 主人只说了唤醒词（或 ASR 只听清了这几个字）→ 误识别。
        #    只要含有唤醒词以外的字，就一律放行去 LLM。
        wake_words = conn.config.get("wakeup_words") or []
        if not (filtered_text and filtered_text in wake_words):
            return ""

        # ④ 命中了 —— 但**不再一律静默丢弃**（23:40 干等 18 秒的教训）。
        #    刚播完问候 1.5s 内：唤醒词音频残留，主人还没开口 → 静默丢。
        #    超过 1.5s：主人明明开口了却只听清唤醒词 = 没听清 → 给个提示。
        return "drop" if gap <= SILENT_WINDOW else "prompt"

    except Exception as e:
        # 守卫本身出错绝不能影响正常对话
        try:
            conn.logger.bind(tag=TAG).warning(f"唤醒去重守卫异常，放行: {e}")
        except Exception:
            pass
        return ""
