# -*- coding: utf-8 -*-
"""LLM 用量与额度监控（2026-10-03）

**为什么需要这个**
主人给了通义千问 key 后问「我没看到免费额度，你帮我看看有没有」。
实测发现：**阿里云不提供任何额度查询 API**（试了 /usage /quota /billing /metrics
全 404，响应头里也没有 quota/balance 字段），只有控制台能看。
所以只能**自己记 token**，并在可能出问题时提前预警。

**记的是什么**
`usage.total_tokens`（阿里云每次响应都返回，和 DeepSeek 一样），
按「引擎 + 日期」分桶落到 `bridge/llm_usage.json`。
它**不等于**官方账单，但能回答主人真正关心的问题：
「这台小智一天/一月要烧多少 token？会不会把免费包烧穿？」

**预警的三种信号**（按可靠性排序）
1. **HTTP 429 / "额度不足" / "exceeded"** —— 官方明说没钱了，**这是唯一硬信号**
2. **单次用量突增** —— 比平时高 5 倍以上，可能在循环调用（出 bug 了）
3. **日累计接近经验阈值** —— 按每天估算的免费包容量提醒

**为什么不做"自动切换模型"**
额度耗尽时自动切到下一个 LLM 看着聪明，但会**静默改变行为**——
小智的脑子换了，主人不知道，下一轮回答风格变了才发现。
更好的做法是**播报告知**（"千问额度用完了，我已经换回 DeepSeek"），
让切换是可见的。降级逻辑见 `pick_llm()`。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_USAGE_FILE = _PROJECT_ROOT / "bridge" / "llm_usage.json"

# 每天多少 token 值得提醒（经验值：qwen-plus 免费包 7000万/90天 ≈ 78万/天，
# 但那是新用户大包；个人日常用远低于此。超过 30 万/天就值得看一眼）
DAILY_WARN_TOKENS = 300_000
# 单次调用用量超过这个值视为异常（正常一轮对话 1~3k token）
SINGLE_CALL_ALERT = 20_000


def _load() -> dict:
    try:
        return json.loads(_USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(d: dict) -> None:
    try:
        _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        # 只留最近 60 天，防止文件无限膨胀
        keys = sorted(d.keys())[-60:]
        _USAGE_FILE.write_text(
            json.dumps({k: d[k] for k in keys}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception:
        pass


def record(engine: str, tokens: int, calls: int = 1) -> None:
    """记一次调用。engine 形如 deepseek / qwen / local_ollama。"""
    if tokens <= 0:
        return
    d = _load()
    day = time.strftime("%Y-%m-%d")
    slot = d.setdefault(day, {})
    e = slot.setdefault(engine, {"tokens": 0, "calls": 0, "max_single": 0})
    e["tokens"] += int(tokens)
    e["calls"] += int(calls)
    e["max_single"] = max(int(e.get("max_single", 0)), int(tokens))
    d[day] = slot
    _save(d)


def today() -> dict:
    return _load().get(time.strftime("%Y-%m-%d"), {})


def report(days: int = 7) -> dict:
    d = _load()
    keys = sorted(d.keys())[-days:]
    out = {"by_day": {}, "total_tokens": 0, "total_calls": 0}
    for k in keys:
        day = d[k]
        out["by_day"][k] = day
        for e in day.values():
            out["total_tokens"] += int(e.get("tokens", 0))
            out["total_calls"] += int(e.get("calls", 0))
    return out


# ─────────────────────── 额度耗尽的信号识别 ───────────────────────
# 阿里云报错文案实测样本：
#   "Free quota exhausted" / "额度不足" / "exceeded your current quota"
#   "Insufficient Balance" / HTTP 429 / 403
_QUOTA_PAT = re.compile(
    r"(quota|exceed|insufficient|balance|额度|欠费|余额不足|429)", re.I)


def is_quota_error(err_text: str) -> bool:
    """判断一个错误是不是"额度/余额问题"（区别于网络抖动、参数错误）。"""
    if not err_text:
        return False
    return bool(_QUOTA_PAT.search(err_text))


def alerts() -> list:
    """该播报的风险清单。"""
    out = []
    t = today()
    for eng, e in t.items():
        tok = int(e.get("tokens", 0))
        if tok >= DAILY_WARN_TOKENS:
            out.append(f"{eng} 今天已用 {tok/10000:.1f} 万 token，"
                       f"接近免费包日均容量，建议去控制台看一眼")
        if int(e.get("max_single", 0)) >= SINGLE_CALL_ALERT:
            out.append(f"{eng} 单次调用用了 {e['max_single']} token，"
                       f"可能有循环调用，建议检查")
    return out


def pick_llm(current: str, candidates: list) -> tuple:
    """额度烧穿时挑下一个可用的 LLM。

    返回 (新引擎名, 说明文字)。**只建议不自动切** ——
    静默换模型会让主人不知道小智的脑子变了，下一轮风格突变才发现。
    """
    for c in candidates:
        if c != current:
            return c, f"{current} 出问题了，建议换 {c}（在管理台「大模型」卡片点一下）"
    return current, ""


if __name__ == "__main__":
    import json as _j
    print(_j.dumps(report(7), ensure_ascii=False, indent=2))
    print("\n--- 风险 ---")
    for a in alerts():
        print(" ", a)
