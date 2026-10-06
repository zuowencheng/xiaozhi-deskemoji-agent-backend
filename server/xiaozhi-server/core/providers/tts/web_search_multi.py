# -*- coding: utf-8 -*-
"""多引擎联网搜索 provider（2026-10-03）

**为什么重写**：上游 `web_search.py` 只支持 metaso/tavily/serply 三个付费源，
且 tavily 那条链路有三个 bug（key 不进 payload、depth 写死 advanced、摘要被注释）。
主人只给了 1000 次/月的 Tavily 额度，测试一下就用了 7 次 —— 根本不够用。

**本文件的策略：引擎链 + 自动降级 + 各引擎额度记账**

| 引擎 | 免 key？ | 免费额度 | 实测 | 备注 |
|---|---|---|---|---|
| **firecrawl** keyless | ✅ 免注册免 key | 1000 credits/月 | ✅ 1.8s / 油价 8.61 | **主力**（无需任何申请） |
| tavily | ❌ 要 key | 1000 次/月 | ✅ 2.7s | 备用，质量也不错 |
| metaso(秘塔) | ❌ 要 key | 有 | 未测 | 上游原有 |
| serply | ❌ 要 key | 2500 次 | 未测 | 上游原有 |
| **deepseek** web_search | ✅ 用你已有 key | 随 DeepSeek 额度 | ⚠️ HTTP200 但**没真搜** | 见下方说明 |

⚠️ **DeepSeek 官方 web_search 实测记录（2026-10-03）**：
`POST /v1/responses` + `tools:[{"type":"web_search"}]` 返回 HTTP 200，
但 output 节点只有 `['reasoning','message']`，**没有 `web_search_call`**，
模型自己回答"我无法实时联网查询"→ 说明该能力对当前账号（灰度）未开放。
试过 tool_choice / web_search_options / include 三种变体均无效；
`/v1/chat/completions` 塞 web_search 类型的 tool 直接 422（只认 function）。
**所以 DeepSeek 目前不能当搜索用**，保留在链尾作为将来灰度开放的备口。

**设计要点**
1. 每个引擎独立配置 `enabled` / `api_key` / `priority`，链式降级
2. 每次调用都记账（`bridge/search_quota.json`），15% 阈值提醒（复用 notify 播报）
3. 引擎全部失败时，返回"我搜不到 + 原因"，**不编造**
4. 答案里带来源 URL，LLM 可以自己核对
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ───────────────────────── 额度记账 ─────────────────────────
def _quota_path() -> Path:
    return _PROJECT_ROOT / "bridge" / "search_quota.json"


def _load_q() -> dict:
    try:
        return json.loads(_quota_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_q(d: dict) -> None:
    try:
        p = _quota_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# 引擎默认免费额度（次/月）。keyless 的 firecrawl 是 1000，tavily 1000。
_ENGINE_QUOTA = {"firecrawl": 1000, "tavily": 1000, "metaso": 1000, "serply": 2500}


def _bump(engine: str, n: int = 1) -> dict:
    d = _load_q()
    k = str(time.strftime("%Y-%m"))
    slot = d.setdefault(k, {})
    e = slot.setdefault(engine, {"used": 0, "quota": _ENGINE_QUOTA.get(engine, 1000)})
    e["used"] = int(e.get("used", 0)) + n
    d[k] = slot
    # 只留最近 3 个月
    keys = sorted(d.keys())[-3:]
    _save_q({x: d[x] for x in keys})
    return e


def quota_report() -> list:
    d = _load_q()
    k = str(time.strftime("%Y-%m"))
    slot = d.get(k, {})
    out = []
    for eng, total in _ENGINE_QUOTA.items():
        e = slot.get(eng) or {}
        used = int(e.get("used", 0))
        out.append({"engine": eng, "used": used, "quota": total,
                    "left": max(0, total - used),
                    "percent": round(used / total * 100, 1) if total else 0})
    return out


def quota_alerts(threshold: float = 0.85) -> list:
    """返回超过阈值的引擎（供 notify 播报）。"""
    return [r for r in quota_report() if r["percent"] >= threshold * 100]


# ───────────────────────── 各引擎实现 ─────────────────────────
def _fmt_results(items: list, max_n: int = 4) -> str:
    """统一结果格式：标题 + 摘要 + 来源链接（LLM 需要链接来判断可信度）。"""
    lines = []
    for i, it in enumerate(items[:max_n], 1):
        title = (it.get("title") or "无标题").strip()
        desc = (it.get("description") or it.get("content") or "").strip()
        url = (it.get("url") or it.get("link") or "").strip()
        seg = f"{i}. {title}"
        if desc:
            seg += f"：{desc[:320]}"
        if url:
            seg += f"（来源：{url}）"
        lines.append(seg)
    return "\n".join(lines)


def search_firecrawl(query: str, max_n: int = 4, timeout: float = 25.0) -> dict:
    """Firecrawl v2 keyless —— **免 key、免注册**，主力引擎。

    实测 2026-10-03：1.8s 返回，「北京今日92号汽油」直接给出 8.61 元/升，
    且多源交叉一致（比 Tavily 的 answer 更聚焦，answer 反而常是英文）。
    """
    r = httpx.post("https://api.firecrawl.dev/v2/search",
                   json={"query": query, "limit": max_n,
                         "sources": ["web"], "lang": "zh", "country": "cn"},
                   timeout=timeout)
    if r.status_code == 200:
        j = r.json()
        data = j.get("data") or {}
        items = data.get("web") or data.get("results") or []
        if items:
            return {"ok": True, "items": items}
        return {"ok": False, "error": "无结果"}
    return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:140]}"}


def search_tavily(query: str, max_n: int = 4, api_key: str = "",
                  timeout: float = 25.0) -> dict:
    """Tavily（要 key，1000 次/月）。备用。

    ⚠️ key **必须放 payload**，只放 Authorization 头会 401（2026-10-03 踩过）。
    """
    if not api_key:
        return {"ok": False, "error": "未配置 key"}
    r = httpx.post("https://api.tavily.com/search",
                   json={"api_key": api_key, "query": query, "max_results": max_n,
                         "search_depth": "basic", "include_answer": True},
                   timeout=timeout)
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:140]}"}
    j = r.json()
    items = [{"title": x.get("title"), "description": x.get("content"),
              "url": x.get("url")} for x in (j.get("results") or [])]
    if not items:
        return {"ok": False, "error": "无结果"}
    return {"ok": True, "items": items,
            "answer": j.get("answer") or ""}


def search_metaso(query: str, max_n: int = 4, api_key: str = "",
                  timeout: float = 25.0) -> dict:
    """秘塔 metaso（要 key，mk- 前缀）。"""
    if not api_key:
        return {"ok": False, "error": "未配置 key"}
    r = httpx.post("https://metaso.cn/api/v1/search",
                   headers={"Authorization": f"Bearer {api_key}"},
                   json={"query": query, "scope": "web", "size": max_n},
                   timeout=timeout)
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:140]}"}
    j = r.json()
    items = [{"title": (x.get("title") or "").strip(),
              "description": (x.get("snippet") or x.get("description") or "").strip(),
              "url": x.get("url")} for x in (j.get("data") or {}).get("webpages", {}).values()] \
        if isinstance((j.get("data") or {}).get("webpages"), dict) else \
        [{"title": (x.get("title") or ""), "description": (x.get("snippet") or ""),
          "url": x.get("url")} for x in ((j.get("data") or {}).get("webpages") or [])]
    return {"ok": bool(items), "items": items}


def search_serply(query: str, max_n: int = 4, api_key: str = "",
                  timeout: float = 25.0) -> dict:
    """Serply（要 key，2500 次一次性）。"""
    if not api_key:
        return {"ok": False, "error": "未配置 key"}
    r = httpx.get("https://api.serply.io/v1/search",
                  params={"q": query, "num": max_n, "gl": "cn", "hl": "zh"},
                  headers={"X-API-KEY": api_key, "X-Proxy-Location": "CN"},
                  timeout=timeout)
    if r.status_code != 200:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:140]}"}
    j = r.json()
    items = [{"title": x.get("title"), "description": x.get("snippet"),
              "url": x.get("link")} for x in (j.get("results") or [])]
    return {"ok": bool(items), "items": items}


def search_deepseek(query: str, api_key: str = "", timeout: float = 60.0) -> dict:
    """DeepSeek 官方 web_search（Responses API）—— **目前不可用，留作备口**。

    2026-10-03 实测：HTTP 200，但 output 节点里没有 `web_search_call`，
    模型自答"我无法实时联网查询"。→ 该能力对当前账号未开放（灰度）。
    将来若开放，这里不用改上游，只把 ok 判定放开即可。
    """
    if not api_key:
        return {"ok": False, "error": "未配置 key"}
    try:
        r = httpx.post("https://api.deepseek.com/v1/responses",
                       headers={"Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json"},
                       json={"model": "deepseek-v4-flash", "input": query,
                             "tools": [{"type": "web_search"}]},
                       timeout=timeout)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        j = r.json()
        # ★ 判定依据：必须有 web_search_call 节点，否则等于没搜
        has_call = any("web_search" in str(o.get("type", ""))
                       for o in (j.get("output") or []))
        if not has_call:
            return {"ok": False,
                    "error": "DeepSeek web_search 未对该账号开放（无 web_search_call 节点）"}
        text = ""
        for o in (j.get("output") or []):
            if o.get("type") == "message":
                for c in (o.get("content") or []):
                    text += (c.get("text") or c.get("content") or "")
        return {"ok": bool(text), "items": [{"title": "DeepSeek 联网回答",
                                             "description": text[:1500], "url": ""}]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:80]}"}


ENGINES = {
    "firecrawl": {"fn": search_firecrawl, "needs_key": False,
                  "label": "Firecrawl（免 key 免注册，1000 次/月）"},
    "tavily": {"fn": search_tavily, "needs_key": True,
               "label": "Tavily（1000 次/月，需 tvly- key）"},
    "metaso": {"fn": search_metaso, "needs_key": True,
               "label": "秘塔 metaso（需 mk- key）"},
    "serply": {"fn": search_serply, "needs_key": True,
               "label": "Serply（2500 次，需 key）"},
    "deepseek": {"fn": search_deepseek, "needs_key": True,
                 "label": "DeepSeek 官方搜索（当前账号未开放）"},
}


# ───────────────────────── 对外主入口 ─────────────────────────
def web_search_multi(query: str, cfg: dict, max_results: int = 4,
                     order: list | None = None) -> dict:
    """按 order 依次试引擎，第一个成功就返回。全部失败返回 ok=False。

    cfg 形如（来自 data/.config.yaml 的 plugins.web_search）：
        engines:
          firecrawl: {enabled: true, api_key: ""}     # keyless，key 可空
          tavily:    {enabled: true, api_key: "tvly-..."}
        order: [firecrawl, tavily, metaso, serply]
    """
    eng_cfg = (cfg or {}).get("engines") or {}
    seq = order or (cfg or {}).get("order") or ["firecrawl", "tavily", "metaso", "serply"]
    tried, errs = [], {}
    for eng in seq:
        meta = ENGINES.get(eng)
        if not meta:
            continue
        ec = eng_cfg.get(eng) or {}
        if ec.get("enabled") is False:
            continue
        key = (ec.get("api_key") or "").strip()
        if meta["needs_key"] and not key:
            errs[eng] = "未配置 key，已跳过"
            continue
        t0 = time.time()
        try:
            r = meta["fn"](query, max_results, key) if meta["needs_key"] \
                else meta["fn"](query, max_results)
        except Exception as e:
            errs[eng] = f"{type(e).__name__}: {str(e)[:70]}"
            continue
        if r.get("ok") and r.get("items"):
            _bump(eng)
            return {"ok": True, "engine": eng, "engine_label": meta["label"],
                    "items": r["items"], "answer": r.get("answer", ""),
                    "elapsed": round(time.time() - t0, 2),
                    "failed": errs}
        errs[eng] = r.get("error") or "无结果"
        tried.append(eng)
    return {"ok": False, "failed": errs, "tried": tried}


def search_text(query: str, cfg: dict, max_results: int = 4) -> str:
    """给 LLM 用的返回文本。**搜不到就明说搜不到，绝不编造。**"""
    r = web_search_multi(query, cfg, max_results)
    if not r.get("ok"):
        detail = "；".join(f"{k}: {v}" for k, v in (r.get("failed") or {}).items())
        return (f"【联网搜索失败】没能搜到「{query}」的结果。\n"
                f"各搜索通道情况：{detail}\n"
                f"请直接告诉用户搜不到，不要凭记忆编造答案。")
    head = f"【联网搜索】来源：{r['engine_label']}（{r['elapsed']}s）\n"
    body = _fmt_results(r["items"], max_results)
    ans = (r.get("answer") or "").strip()
    tail = ""
    if ans:
        tail = ("\n【引擎给的结论】" + ans[:500]
                + "\n（若结论是英文，请用中文转述给用户）")
    return head + body + tail
