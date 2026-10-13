#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""quota.py —— 火山语音合成额度账本（1.0 / 2.0 双通道）

需求：主人两个版本都开通了，各有 20000 字符免费额度（2026-10-03 截图确认：
  语音合成2.0 已开通 · 剩余 19,340 / 20,000 字符 · 标准音色 99+
  语音合成1.0 已开通 · 剩余 19,806 / 20,000 字符 · 标准音色 114+）
要求：**先优先用 2.0，用完了自动降到 1.0，并在快用完时主动提醒**。

数据从哪来 —— 火山 v3 响应体最后一块会返回精确计费字符数：
    {"code":20000000,"message":"OK","usage":{"text_words":26}}
所以**不是估算，是真值**。前提是请求头带：
    X-Control-Require-Usage-Tokens-Return: *

存储：bridge/quota.json（纯 JSON，人能看、能手改）
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
FILE = BASE / "quota.json"

# 低水位比例：剩余额度低于该比例就提前切到下一通道（2026-10-13 加）
_LOW_RATIO = 0.05

# 通道定义：rid + 各自的默认音色 + 额度上限（控制台截图值）
CHANNELS = {
    "2.0": {
        "rid": "seed-tts-2.0",
        "speaker": "zh_female_vv_uranus_bigtts",
        "name": "豆包语音合成2.0",
        "quota": 20000,
        "order": 0,                       # 越小越优先
    },
    "1.0": {
        "rid": "volc.service_type.10029",
        "speaker": "zh_female_wanwanxiaohe_moon_bigtts",
        "name": "语音合成1.0",
        "quota": 20000,
        "order": 1,
    },
}

_LOCK = threading.Lock()
_ALERT_RATIO = 0.15          # 剩余低于 15% 就提醒
_ALERT_COOLDOWN = 6 * 3600   # 同一条提醒 6 小时内不重复


def _load() -> dict:
    if FILE.exists():
        try:
            return json.loads(FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"channels": {}}


def _save(data: dict):
    tmp = FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, FILE)


def _ensure(data: dict, ch: str):
    d = data.setdefault("channels", {})
    if ch not in d:
        d[ch] = {"used": 0, "alerted_at": 0, "switched_at": 0}
    return d[ch]


# ---------------- 写入 ----------------

def add_usage(ch: str, chars: int) -> dict:
    """累加一次合成的计费字符数。chars<=0 时忽略。"""
    try:
        chars = int(chars)
    except Exception:
        return {}
    if chars <= 0:
        return {}
    with _LOCK:
        data = _load()
        rec = _ensure(data, ch)
        rec["used"] = int(rec.get("used", 0)) + chars
        rec["last_at"] = int(time.time())
        data["channels"][ch] = rec
        _save(data)
    return remaining(ch)


def set_quota(ch: str, total: int):
    """控制台改了额度就手动同步一下。"""
    with _LOCK:
        data = _load()
        rec = _ensure(data, ch)
        rec["quota"] = int(total)
        _save(data)


# ---------------- 查询 ----------------

def remaining(ch: str) -> dict:
    data = _load()
    rec = data.get("channels", {}).get(ch, {})
    total = int(rec.get("quota") or CHANNELS.get(ch, {}).get("quota", 0))
    used = int(rec.get("used", 0))
    left = max(0, total - used)
    # ★ 2026-10-13：低水位提前切换。此前 exhausted 只看 left<=0，
    #   结果 2.0 剩 545 字符时仍被 pick_channel 选中，再说 8 句就归零，
    #   用户会在一句话说到一半时突然换音色（自动兜底虽不哑，但体验断裂）。
    #   低于 5% 就视为"该换了"，让切换发生在开口之前。
    return {
        "channel": ch,
        "name": CHANNELS.get(ch, {}).get("name", ch),
        "rid": CHANNELS.get(ch, {}).get("rid", ""),
        "speaker": CHANNELS.get(ch, {}).get("speaker", ""),
        "used": used,
        "quota": total,
        "remaining": left,
        "percent": round(left / total * 100, 1) if total else 0.0,
        "exhausted": left <= 0,
        "low": total > 0 and 0 < left <= total * _LOW_RATIO,
    }


def stats() -> list:
    return [remaining(ch) for ch in CHANNELS]


def need_alert(ch: str = None) -> list:
    """该提醒的通道：剩余低于阈值且冷却期已过。"""
    out = []
    now = int(time.time())
    for ch in ([ch] if ch else list(CHANNELS)):
        info = remaining(ch)
        if info["quota"] <= 0 or info["exhausted"]:
            continue
        if info["remaining"] / info["quota"] > _ALERT_RATIO:
            continue
        rec = _load().get("channels", {}).get(ch, {})
        last = int(rec.get("alerted_at", 0))
        if last and now - last < _ALERT_COOLDOWN:
            continue
        out.append(info)
    return out


def mark_alerted(ch: str):
    with _LOCK:
        data = _load()
        rec = _ensure(data, ch)
        rec["alerted_at"] = int(time.time())
        _save(data)


def pick_channel() -> str | None:
    """按优先级挑还能用的通道：2.0 优先，低于低水位或用完自动落到 1.0。都没有则 None。"""
    for ch, meta in sorted(CHANNELS.items(), key=lambda kv: kv[1]["order"]):
        info = remaining(ch)
        if not (info["exhausted"] or info["low"]):
            return ch
    return None


def current() -> str | None:
    return pick_channel()


def describe(ch: str) -> str:
    info = remaining(ch)
    return (f"{info['name']}已用 {info['used']} / {info['quota']} 字符，"
            f"还剩 {info['remaining']}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "add":
        print(json.dumps(add_usage(sys.argv[2], int(sys.argv[3])), ensure_ascii=False))
    else:
        print(json.dumps({"current": pick_channel(), "channels": stats(),
                          "need_alert": [c["channel"] for c in need_alert()]},
                         ensure_ascii=False, indent=2))
