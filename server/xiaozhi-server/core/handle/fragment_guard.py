# -*- coding: utf-8 -*-
"""语音碎片缓冲 —— 解决"话没说完它就抢答"（2026-10-03）

## 病症（主人原话「我俩对不上号，相当于频率对不上」）

18:14 那轮的真实日志：
```
18:14:21  识别文本: 你好。        ← 主人的一句话被切成第 1 段
18:14:24  语音生成: 我在呢
18:14:27  收到 listen start     ← 设备立刻又开录
18:14:31  识别文本: 现在。        ← 第 2 段
18:14:33  语音生成: 现在？
18:14:35  语音生成: 你说一半我这边听得一头雾水   ← 它自己都在抱怨
18:14:52  识别文本: 我说现在。    ← 第 3 段
18:14:56  语音生成: 你倒是把后半句给我
```
一句话被 VAD 切成 3~7 段，**每段它都当成完整请求去回答**，
所以主人感觉"没说完它就插嘴"。

## 为什么不能只靠调大判停解决

判停窗口调大 → 切得少了，但会变"迟迟不断句"（说完了它还在等），
两个方向都是坏的。**所以要两道防线**：
1. 第一道（固件/配置）：把判停调到合理值（VAD 1400 + 火山 1200 = 2600ms）
2. 第二道（本模块）：**对明显是碎片的短片段先攒着，别急着答**

## 判据（刻意保守：宁可少答，不可乱答）

只有**同时**满足下面之一才认为是碎片：
- 去掉标点后 < 4 字（"你好。" "现在。" "嗯"）
- 4~10 字且以悬空词结尾（现在/然后/就是/那个/我/你/他/这/那/但是/因为）

**明确放行**的（不能被拦）：
- 完整短命令：几点了 / 退下 / 打开网易云 / 天气
- 长度 > 10 字：基本一定是完整句
- 命中退出指令（先于本模块处理）

## 攒够什么条件才放行
- 攒到 **2 段**（拼起来可能是个完整句）
- 或第一段之后**等 6 秒**没等到下一段（主人说完了只是停顿）
- 或新来的片段**不是碎片**（说明前面那个是碎片结束了）

攒够后把片段用「，」拼起来一次性给 LLM。
"""
from __future__ import annotations

import re
import time

# 悬空词：句子说到一半卡在这些词上，后面肯定还有内容
_DANGLING = (
    "现在", "然后", "就是", "那个", "这个", "但是", "因为", "所以",
    "我", "你", "他", "她", "它", "这", "那", "如果", "要是",
)
# 完整短命令白名单（命中直接放行，绝不缓冲）
_ALLOW = (
    "几点了", "几点", "退下", "再见", "结束对话", "停", "音量", "静音",
    "天气", "油价", "打开", "关闭", "搜索", "查一下", "帮我",
)
_PUNCT = re.compile(r"[，。！？、；：\s,.!?;:]+")

#: 日志 tag（与 notify_plugin 保持一致的命名习惯）
tag = "fragment_guard"


def unwrap(text: str) -> str:
    """剥掉 ASR 的 JSON 包装，拿到纯文本。

    ⚠️ 2026-10-03 踩坑：`asr/base.py` 传给下游的是 **JSON 字符串**
    （`{"content":"你还不。"}` 或 `{"text":...,"speaker":...}`），
    不是纯文本。我最初直接拿它判碎片，**全部判成 False**，
    于是一行"碎片保护"的日志都没有 —— 保护形同虚设。
    **凡是判断"用户说了什么"的逻辑，都必须先过这一层。**
    """
    s = (text or "").strip()
    if not (s.startswith("{") and s.endswith("}")):
        return s
    try:
        import json
        d = json.loads(s)
        if isinstance(d, dict):
            for k in ("content", "text", "result"):
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
    except Exception:
        pass
    return s


def _core(text: str) -> str:
    """去标点后的核心内容。"""
    return _PUNCT.sub("", text or "")


def is_fragment(text: str) -> bool:
    """判断一段识别结果是不是「明显没说完的碎片」。"""
    c = _core(unwrap(text))
    if not c:
        return True                     # 纯标点/空白
    for a in _ALLOW:
        if a in c:
            return False                # 白名单命令，放行
    if len(c) < 4:
        return True
    if len(c) <= 10:
        for d in _DANGLING:
            if c.endswith(d):
                return True
    return False


class FragmentBuffer:
    """按连接维护碎片缓冲。挂在 conn 上（每设备独立）。"""

    MAX_WAIT = 6.0      # 秒：等不到下一段就放行
    MAX_KEEP = 3        # 最多攒几段（再多就不像一句话了）

    def __init__(self):
        self.parts: list = []       # [(text, ts)]
        self.timer = None
        self.flushing = False
        # 事件循环引用（用于超时放行）。没绑上也不影响 —— 只是不会自动超时，
        # 下一次 add() 时会按 MAX_WAIT 判断是否已超期。
        self._loop_ref = None
        # 所属连接：超时时需要它才能把攒的内容送去回答
        self._conn = None

    def clear(self):
        self.parts.clear()
        self.flushing = False
        if self.timer:
            self.timer.cancel()
            self.timer = None

    def joined(self) -> str:
        return "，".join(p[0].rstrip("。！？，") for p in self.parts)

    def add(self, text: str) -> str:
        """加一段。返回可放行给 LLM 的文本，'' 表示先攒着。

        ⚠️ **返回的必须是纯文本**，不是 ASR 的 JSON 包装 ——
        下游 startToChat 会把它当普通句子处理（还会再解析一次 JSON 拿 content），
        这里若把 `{"content":"..."}` 原样传下去，拼出来的就是一堆 JSON 片段。
        """
        text = unwrap(text)
        now = time.time()
        # 超过等待时间 → 之前攒的连同本段一起放行
        if self.parts and now - self.parts[-1][1] > self.MAX_WAIT:
            self.parts.append((text, now))
            return self._release()

        frag = is_fragment(text)
        self.parts.append((text, now))

        if not frag:
            # 本段是完整句 → 前面的碎片是废话，直接丢掉，只答本段
            if len(self.parts) > 1:
                self.parts = [(text, now)]
            return self._release()

        if len(self.parts) >= self.MAX_KEEP:
            return self._release()

        if self.timer:
            self.timer.cancel()
        loop_flush = self._loop_ref
        self.timer = loop_flush.call_later(self.MAX_WAIT, self._on_timeout) \
            if loop_flush else None
        return ''   # 先攒着，不答

    def _release(self) -> str:
        if not self.parts:
            return ''
        out = self.joined()
        self.clear()
        return out

    def _on_timeout(self):
        """等超时了还没等到下一段 → 主人说完了，只是停顿。

        ⚠️ 2026-10-03 修设计缺陷：原来这里只 `self._release()` 把文本丢掉，
        **没人拿去回答** → 主人说完一句完整的话，如果它被切成两段，
        第一段被吞掉、第二段又恰好是完整句（走了"丢弃碎片只答本段"分支），
        就会出现"**小智完全没反应**"。

        现在超时时**主动把攒的内容送去 LLM**（fire-and-forget 协程），
        保证"攒着"不等于"吞掉"。
        """
        out = self._release()
        if not out:
            return
        conn = self._conn
        if conn is None:
            return
        try:
            import asyncio
            from core.handle.receiveAudioHandle import startToChat
            asyncio.create_task(startToChat(conn, out))
            if hasattr(conn, "logger"):
                conn.logger.bind(tag=tag).info(
                    f"[碎片保护] 攒够 {self.MAX_WAIT:.0f}s 仍无后续，"
                    f"主动送出回答: {out!r}")
        except Exception as e:  # 绝不因缓冲失败而影响对话
            if hasattr(conn, "logger"):
                conn.logger.bind(tag=tag).debug(f"碎片保护超时送出失败: {e}")

    def bind_loop(self, loop, conn=None):
        self._loop_ref = loop
        if conn is not None:
            self._conn = conn


def get_buffer(conn) -> FragmentBuffer:
    """取（并在必要时初始化）这条连接的碎片缓冲。"""
    buf = getattr(conn, "_frag_buf", None)
    if buf is None:
        buf = FragmentBuffer()
        try:
            import asyncio
            buf.bind_loop(asyncio.get_running_loop(), conn)
        except Exception:
            buf._conn = conn
        setattr(conn, "_frag_buf", buf)
    return buf
