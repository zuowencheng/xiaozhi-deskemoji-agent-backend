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

# ────────────────────────────────────────────────────────────────
# 2026-10-13 新增：唤醒词**前缀**剥离
# ────────────────────────────────────────────────────────────────
# 老判据（文本"恰好等于"唤醒词）挡不住真实场景：ASR 会把唤醒词那一段
# 音频**一起识别进主人的话里**，于是识别结果是「你好，小智。你现在」
# 这种"唤醒词 + 半截话"。它不等于任何唤醒词 → 老闸放行 → 大模型又
# 回一句「我在呢，怎么了？」—— 和固定开场白「在呢，说吧」撞车，
# 主人一次唤醒听到两遍招呼。日志实锤：
#     20:53:48 语音生成成功: 来了            ← 本地固定开场白
#     20:53:51 识别文本: 你好，小智。你现在
#     20:53:53 语音生成成功: 我在呢 / 怎么了？ ← 大模型第二遍
#
# 所以本轮已打过招呼时，先把开头的唤醒词**剥掉**，再判断剩下的是什么：
#   剩下寒暄/残片 → 不再惊动大模型（否则又是第二遍开场白）
#   剩下真问题     → 用剥离后的文本送大模型（"打开酷狗"照样执行）
#
# ⛔ 铁律不变：**宁可多说一个字，绝不丢主人的真问题。**
#   拿不准就放行送 LLM；实在听不清的走 prompt 回一声「嗯？」，
#   绝不让主人对着空气干等（23:40 那次 18 秒的教训）。

# 剥掉唤醒词后剩下的这些 = 纯寒暄，设备刚说过「在呢，说吧」，不必再回
CHITCHAT_REMAINDER = {
    "你好", "您好", "你好啊", "你好呀", "你们好", "在吗", "在么", "在不在",
    "嗨", "嘿", "哈喽", "哈罗", "hello", "hi", "喂", "哎", "唉", "啊",
    "嗯", "哦", "小智", "小志", "在", "吗",
}

# 剥离后剩余有效字数 ≤ 这个值 → 判定为"只听清个头/话被截断"，不再送 LLM。
# 取 3：「打开酷狗」(4) 会放行，「你现在」(3) 会被拦 —— 后者正是那句
# 让人二次寒暄的元凶。真被误拦时走 prompt 回「嗯？」，主人会自然重说。
REMAINDER_MIN_LEN = 4

_PUNCT = set("，。,.！!？?、；;：:…~～-—《》「」『』（）()[]【】\"' \t\r\n")


def _clean(text: str) -> str:
    """去掉标点/空白，转小写。用于比较，不用于送 LLM。"""
    return "".join(c.lower() for c in (text or "") if c not in _PUNCT)


def _strip_leading(raw: str, word: str):
    """在**原文**上剥离开头的唤醒词（忽略标点/空白/大小写）。

    返回剥离后的原文；不匹配返回 None。
    必须基于原文切，不能拿清洗后的串去送 LLM —— 那是「你好小智你现在」
    这种没有标点的怪句子，大模型会读得很别扭。
    """
    idx_map, cleaned = [], []
    for i, ch in enumerate(raw or ""):
        if ch in _PUNCT:
            continue
        cleaned.append(ch.lower())
        idx_map.append(i)
    cl = "".join(cleaned)
    wc = _clean(word)
    if not wc or not cl.startswith(wc):
        return None
    cut = idx_map[len(wc)] if len(wc) < len(idx_map) else len(raw)
    return (raw[cut:]).lstrip("，。,.！!？?、；;：: \t")


def strip_wakeup_prefix(conn, raw_text: str):
    """本轮已播过问候 → 把混进来的唤醒词前缀剥掉，再决定怎么处理。

    返回 (action, text)：
        ("",       原文本) → 没命中，照常送 LLM
        ("drop",   "")     → 静默丢（刚播完问候，这是唤醒词音频残留）
        ("prompt", "")     → 回一声「嗯？」提示重说
        ("strip",  剥离文) → 用剥离后的**原文**送 LLM

    异常一律返回 ("", 原文本) —— 守卫出错绝不能影响正常对话。
    """
    try:
        if not raw_text:
            return "", raw_text

        # ① 本轮是否播过问候。没播过 → 这次是真唤醒，原样放行。
        if not getattr(conn, "greeting_session_id", None):
            return "", raw_text

        # ② 超时放行（与老守卫同一判据）：主人隔很久才说下一句，那是真需求。
        import time
        gap_cfg = float(conn.config.get("greeting_debounce", 600.0))
        gap = time.time() - float(getattr(conn, "greeting_played_at", 0.0))
        if gap > gap_cfg:
            return "", raw_text

        wake_words = conn.config.get("wakeup_words") or []
        if not wake_words:
            return "", raw_text

        # ③ 长的唤醒词优先，避免「你好小智」被「你好」抢先切掉只留「小智」
        for w in sorted(wake_words, key=lambda x: -len(str(x))):
            rest = _strip_leading(raw_text, w)
            if rest is None:
                continue
            rest_clean = _clean(rest)
            # 剥完什么都没了，或只剩寒暄/残片 → 不再让大模型二次寒暄
            if (not rest_clean
                    or rest_clean in CHITCHAT_REMAINDER
                    or len(rest_clean) < REMAINDER_MIN_LEN):
                return ("drop" if gap <= SILENT_WINDOW else "prompt"), ""
            return "strip", rest

        return "", raw_text

    except Exception as e:
        try:
            conn.logger.bind(tag=TAG).warning(f"唤醒词前缀剥离异常，放行: {e}")
        except Exception:
            pass
        return "", raw_text


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


# ★ 2026-10-14 「自听自」过滤
#
#   设备侧有几句**本地预合成**的开场/退场语音（不经服务端 TTS）：
#       手势唤醒：「我来啦，您请讲」
#       手势退下：「那我退下了」
#   固件为了"唤醒后立刻能听到主人说话"，会在播这些语音时**就把麦克风打开**
#   （听不听得见见铁律：半双工下两者不可兼得，只能二选一）。
#   于是麦克风把自己喇叭里的话原封不动收进去 → ASR 忠实识别 →
#   大模型收到「我来啦，您请讲」→ 一本正经回答一长串。
#   日志实锤（02:13:10 / 02:13:52 两次）：
#       识别文本: 我来啦，您请讲。
#       大模型收到用户消息: 我来啦，您请讲。
#       发送第一段语音: 哟……（连说 12 段，纯烧额度）
#
#   这道闸在**所有语音输入的总入口**（core/providers/asr/base.py）生效，
#   命中就直接丢弃，不进 LLM。
DEVICE_SELF_SPEECH = (
    "我来啦，您请讲",
    "那我退下了",
)

_PUNCT = "，。！？、,.!?~ 　"


def _norm(t: str) -> str:
    return "".join(ch for ch in (t or "") if ch not in _PUNCT)


def is_device_self_speech(text: str) -> bool:
    """识别结果是不是设备自己刚播出去的那句话（自听自）。"""
    try:
        n = _norm(text)
        if not n:
            return False
        for phrase in DEVICE_SELF_SPEECH:
            np_ = _norm(phrase)
            # 完全命中，或 ASR 只听清了开头几个字 —— 都算自听自
            if n == np_ or n.startswith(np_) or np_.startswith(n):
                return True
        return False
    except Exception:
        return False
