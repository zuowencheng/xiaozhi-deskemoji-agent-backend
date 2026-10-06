#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""exit_command.py —— 「退下吧」类指令的真实待机处理

2026-10-03 主人反馈：
    「我跟他说'退下吧，退下'，他好像说是自己退出了，但实际还是在后台聆听当中。
      我希望在对他说'退下、退下吧、再见、先这样吧、回头再聊、你歇会儿吧、结束对话'，
      然后就可以真实地处于待机状态了。」

问题本质：**LLM 只是口头答应，设备的麦克风还在往下送音频**，
服务端照单全收去跑 ASR，白烧钱还显得没听懂。

做法：在 **进 LLM 之前**拦一道，命中就走短路 —— 回一句告别 + 发 standby 让设备真待机。

为什么不用 LLM 判定：这类指令是固定的几个词，走 LLM 既慢又会漂移
（模型可能说"好的我先退下了"却没真退），而且要多烧一次 token。
"""
from __future__ import annotations

import json
import re

# 主人给的那批 + 常见同义说法。
# 匹配策略：归一化后做「整句包含」，且**不允许**还有其他实词，
# 否则「退下吧顺便查下天气」这种也会被误判成退出。
EXIT_WORDS = {
    "退下", "退下吧", "退下了", "你先退下", "你退下", "下去吧",
    "再见", "拜拜", "回头见", "回头再聊", "回头聊",
    "先这样吧", "先这样", "就这样吧", "就到这吧",
    "你歇会儿吧", "你歇会吧", "歇会儿吧", "你先歇着", "你先歇歇",
    "结束对话", "结束", "不聊了", "先不聊了", "不用说了",
    "你可以退下了", "你去休息吧", "你去歇着吧",
}

# 归一化时直接丢弃的噪声字符
_NOISE = re.compile(r"[\s,.。，、!?！？~～…·\-—\"'“”‘’]+")


def normalize(text: str) -> str:
    if not text:
        return ""
    return _NOISE.sub("", str(text)).lower()


def is_exit(text: str) -> bool:
    """是否是退出指令。**要求归一化后整句就是那一句**。"""
    t = normalize(text)
    if not t:
        return False
    if t in EXIT_WORDS:
        return True
    # 允许「你先退下吧」「小智退下」这类主语前缀
    for w in EXIT_WORDS:
        if len(t) <= len(w) + 3 and w in t:
            return True
    return False


async def handle_exit(conn, text: str = "") -> bool:
    """命中退出指令时调用：发 standby 让设备真待机。返回是否已处理。"""
    if not is_exit(text):
        return False
    try:
        # 1) 先让设备进 idle：关麦、回待机、不再往服务端送音频
        await conn.websocket.send(json.dumps({
            "type": "system", "command": "standby"
        }))
        # 2) 顺手清掉服务端侧的状态，避免下一轮还拿旧音频去跑 ASR
        try:
            conn.reset_audio_states()
        except Exception:
            pass
        conn.logger.bind(tag=__name__).info(
            f"识别到退出指令「{text}」，已让设备进入待机")
    except Exception as e:
        try:
            conn.logger.bind(tag=__name__).warning(f"退出指令处理失败: {e}")
        except Exception:
            pass
    return True
