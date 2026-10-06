"""speech_summary.py —— 把任务执行结果总结成能一口听完的口播稿

为什么需要（2026-10-03 主人原话）：
    「如果直接截断，有时候内容会不完整。应该把这个任务做成摘要的形式去回复，
      尽量简明扼要，但要保证是一个完整的回复」

做法：调 LLM（DeepSeek）改写，目标是 **≤80 字的完整句子**；
      LLM 不可用/超时/返回 nonsense 时**降级为原文**（宁可长，也不说残句）。
      硬截断只在 >120 字时兜底，且优先在句号处断开，避免半句话。
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

_SRV = Path(__file__).resolve().parents[1] / "server" / "xiaozhi-server"

MAX_TARGET = 100      # 目标上限（主人要求：不超过 100 字）
HARD_LIMIT = 120      # 硬上限，超过才截断
LLM_TIMEOUT = 12.0    # 秒，超时就用原文


def _llm_conf() -> dict:
    import yaml
    try:
        c = yaml.safe_load(open(_SRV / "data" / ".config.yaml", encoding="utf-8"))
        return c["LLM"]["DeepSeekLLM"]
    except Exception:
        return {}


PROMPT = """把下面这段「任务执行结果」改写成一句**口播稿**，用于语音播报给主人听。

硬性要求：
1. **不超过 80 个字**
2. 必须是**一句完整的话**，主语清楚，主人一听就懂，**不许出现半句话**
3. 去掉客套话、过程描述、搜索过程、英文原文
4. 保留关键信息：做了什么 / 结果如何 / 需要主人做什么
5. 用「你」称呼主人，不要用「他/它」

只输出这句口播稿本身，不要任何解释、引号、markdown。

原文：
{raw}"""


def _clean(text: str) -> str:
    """把 LLM 输出收拾干净：去引号/前缀/换行，压成一个句子。"""
    if not text:
        return ""
    t = str(text).strip()
    t = re.sub(r"^[\"'「『]|[\"'」』]$", "", t).strip()
    t = re.sub(r"^(口播稿|播报|回答|输出)[:：]\s*", "", t).strip()
    t = re.sub(r"^[-*•]\s*", "", t)
    # 多行取最长那行（LLM 有时会先解释两句）
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if lines:
        t = max(lines, key=len)
    t = re.sub(r"\s+", "", t)          # 中文口播不需要空格
    return t.strip("。．.！!？? ")


def _smart_truncate(text: str, limit: int = HARD_LIMIT) -> str:
    """超长才截断，且优先在句号/逗号处断，避免半句话。"""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("。", "！", "？", "；", "，"):
        idx = cut.rfind(sep)
        if idx >= limit * 0.5:          # 断点不能太靠前，否则丢信息
            return cut[:idx + 1]
    return cut + "…"


async def summarize(raw: str, title: str = "") -> str:
    """返回可直接播报的一句话。永不抛异常。"""
    raw = (raw or "").strip()
    if not raw:
        return f"{title}，办好了。" if title else "办好了。"
    # 已经很短的（<=60 字且有句号）就不浪费一次 LLM 调用
    if len(raw) <= 60 and re.search(r"[。！？.!?]", raw):
        return raw

    cfg = _llm_conf()
    api_key = cfg.get("api_key")
    base_url = cfg.get("base_url")
    model = cfg.get("model_name", "deepseek-chat")
    if not (api_key and base_url):
        return _smart_truncate(raw)

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=LLM_TIMEOUT)
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": PROMPT.format(raw=raw[:2000])}],
                temperature=0.3,
                max_tokens=200,
            ),
            timeout=LLM_TIMEOUT,
        )
        out = _clean(resp.choices[0].message.content or "")
        # 太短说明 LLM 没干活（可能只回了「好的」），或截断得没法听
        if len(out) < 8:
            return _smart_truncate(raw)
        return _smart_truncate(out)
    except Exception:
        return _smart_truncate(raw)
    finally:
        try:
            del client
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    sample = sys.argv[1] if len(sys.argv) > 1 else (
        "I've checked thoroughly — NetEase Cloud Music (网易云音乐) is not installed on this computer. "
        "What's installed is 酷狗音乐 (Kugou Music). Let me report this honestly rather than guess.\n\n"
        "主人，我找遍了整台电脑，网易云音乐这个软件并没有安装，开始菜单和注册表里都没有它的踪影，"
        "装的是酷狗音乐。要不要我帮您打开酷狗音乐，或者您先装一下网易云音乐？"
    )
    print("原文长度:", len(sample))
    print("口播稿:", asyncio.run(summarize(sample, "打开网易云音乐")))
