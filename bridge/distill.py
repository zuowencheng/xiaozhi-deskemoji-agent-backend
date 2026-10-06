#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""distill.py —— 每日记忆蒸馏（2026-10-03）

主人要求：
    「和��智的日常沟通内容、让它执行任务内容，你也可以做一个每日定时任务，
      每天凌晨后台自动整理汇总当天有记忆有价值的记忆，完善到 WorkBuddy 记忆中，
      这样 WorkBuddy 与小智 都能够同时优化、完善增强后续记忆内容」

流程（对应架构图的 ①②③）：
  ① collect_today()  汇集三方素材：小智记忆 / 任务执行结果 / WorkBuddy 当日日志
  ② distill()        LLM 一次调用抽出「值得记住的事实」，带重要性分与分层建议
  ③ commit()         去重 → 冲突检测（版本链）→ 分层落库 → L3 写进 WorkBuddy MEMORY.md

设计约束：
- **幂等**：同一天重复跑不会产生重复记忆（bridge/distill_state.json 记已处理日期）
- **不阻塞**：只用标准库 + 同步 requests，失败一律降级不抛
- **LLM 不可用**：原样保留当天对话为 chat 类记忆，至少不丢东西
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE = BASE / "distill_state.json"
LOCK = threading.Lock()

_KINDS = ("chat", "profile", "project", "knowledge", "task")
_L3_TAGS = "思维层"


# ---------------------------------------------------------------- 小工具

def _load_mem():
    import importlib.util
    p = BASE / "memory.py"
    if not p.exists():
        return None
    spec = importlib.util.spec_from_file_location("xz_distill_mem", str(p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _llm_conf():
    import yaml
    srv = BASE.parent / "server" / "xiaozhi-server" / "data" / ".config.yaml"
    c = yaml.safe_load(open(srv, encoding="utf-8")) or {}
    return (c.get("LLM") or {}).get("DeepSeekLLM") or {}


def _wb_memory_dir() -> Path | None:
    """找 WorkBuddy 工作区的 .workbuddy/memory 目录（往上找 3 层）。"""
    for up in (1, 2, 3):
        p = (BASE.parents[up] / ".workbuddy" / "memory")
        if p.exists():
            return p
    return None


def _state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"done": {}}


def _save_state(s: dict):
    try:
        STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- ① 采集

def collect_today(day: str = None) -> dict:
    """汇集 day 当天的三类素材。day 格式 YYYY-MM-DD，默认今天。"""
    day = day or datetime.now().strftime("%Y-%m-%d")
    out = {"day": day, "memories": [], "tasks": [], "diary": "", "todos": []}

    mem = _load_mem()
    if mem is not None:
        try:
            for r in mem.recent(200):
                if (r.get("ts") or "")[:10] == day:
                    out["memories"].append(
                        {"kind": r.get("kind"), "content": r.get("content", "")})
        except Exception:
            pass

    # 任务执行结果（当天落盘到 done/ 的）
    done_dir = BASE / "done"
    if done_dir.exists():
        for f in sorted(done_dir.glob("task-*.md")):
            try:
                if datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d") != day:
                    continue
                txt = f.read_text(encoding="utf-8", errors="replace")
                title = _grab(txt, r"##\s*任务内容\s*\n+(.+?)(?:\n##|\Z)")
                result = _grab(txt, r"##\s*执行结果\s*\n+(.+?)(?:\n##|\Z)") or ""
                out["tasks"].append({"title": title, "result": result.strip()[:800]})
            except Exception:
                continue

    # WorkBuddy 当日日志
    wdir = _wb_memory_dir()
    if wdir:
        f = wdir / f"{day}.md"
        if f.exists():
            try:
                out["diary"] = f.read_text(encoding="utf-8", errors="replace")[:6000]
            except Exception:
                pass

    # 当天未完成的待办（值得记住的"挂账"）
    try:
        import todo
        out["todos"] = [t.get("title") for t in todo.list_todos("pending", 20)]
    except Exception:
        pass

    return out


def _grab(text: str, pattern: str) -> str:
    m = re.search(pattern, text, re.S)
    return " ".join(m.group(1).split()) if m else ""


# ---------------------------------------------------------------- ② 蒸馏

PROMPT = """下面是「{day}」这一天里，一台桌面智能体（小智）产生的素材。
请提取其中**值得长期记住**的事实。

判定标准（宁缺毋滥）：
- 该记：关于主人的偏好/习惯/身份、项目的进展与决策、踩过的坑与结论、
        约定俗成的规则、以后还会用到的知识
- 不记：一次性的寒暄、临时状态（"正在播放音乐"）、重复的已有信息、客套话

输出 JSON 数组，每个元素：
  {{
    "content": "一句话陈述，主语清楚，脱离上下文也能看懂，40字以内",
    "kind": "profile | project | knowledge | task",
    "importance": 1到5的整数（5=务必长期保留，1=可有可无）,
    "is_l3": true或false   // true 表示这是方法论/决策/教训，值得进思维层长期复用
  }}

只输出 JSON，不要任何解释文字。

素材：
{material}
"""


def distill(material: dict) -> list[dict]:
    """调 LLM 抽取。失败返回空列表（调用方负责降级）。"""
    if not material:
        return []
    parts = []
    if material.get("memories"):
        parts.append("【小智当天的对话与记录】\n" + "\n".join(
            f"- [{m['kind']}] {m['content']}" for m in material["memories"]))
    if material.get("tasks"):
        parts.append("【当天执行的任务与结果】\n" + "\n".join(
            f"- {t['title']} → {t['result']}" for t in material["tasks"]))
    if material.get("diary"):
        parts.append("【助手当天的排障/项目日志】\n" + material["diary"])
    if material.get("todos"):
        parts.append("【当前未完成的待办】\n" + "\n".join(f"- {t}" for t in material["todos"]))
    if not parts:
        return []

    text = "\n\n".join(parts)[:12000]
    cfg = _llm_conf()
    if not (cfg.get("api_key") and cfg.get("base_url")):
        return []
    try:
        from openai import OpenAI
        cli = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=90)
        r = cli.chat.completions.create(
            model=cfg.get("model_name", "deepseek-chat"),
            messages=[{"role": "user", "content": PROMPT.format(
                day=material["day"], material=text)}],
            temperature=0.2, max_tokens=2000,
        )
        raw = (r.choices[0].message.content or "").strip()
        m = re.search(r"\[.*\]", raw, re.S)
        if not m:
            return []
        items = json.loads(m.group(0))
        out = []
        for it in items if isinstance(items, list) else []:
            c = (it.get("content") or "").strip()
            if not c:
                continue
            k = it.get("kind") if it.get("kind") in _KINDS else "knowledge"
            try:
                imp = max(1, min(5, int(it.get("importance", 3))))
            except Exception:
                imp = 3
            out.append({"content": c, "kind": k, "importance": imp,
                        "is_l3": bool(it.get("is_l3"))})
        return out
    except Exception:
        return []


# ---------------------------------------------------------------- ③ 落库

def _similar(mem, content: str, thresh: float = 0.82):
    try:
        hits = mem.search(content, limit=3)
    except Exception:
        return None
    for h in hits or []:
        if float(h.get("score", 0)) >= thresh:
            return h
    return None


def commit(items: list[dict], day: str = None, write_wb: bool = True) -> dict:
    """去重 → 冲突标记 → 分层落库 → L3 写 WorkBuddy。"""
    day = day or datetime.now().strftime("%Y-%m-%d")
    mem = _load_mem()
    stat = {"added": 0, "duplicated": 0, "superseded": 0, "l3": 0, "wb_lines": 0}
    if not mem or not items:
        return stat

    l3_lines = []
    for it in items:
        c = it["content"].strip()
        try:
            hit = _similar(mem, c)
        except Exception:
            hit = None
        if hit:
            # 已有相似记忆：新记忆标为取代，旧记忆只标记不删
            stat["duplicated"] += 1
            if (it.get("importance", 3) >= 4
                    and (hit.get("content") or "") != c):
                stat["superseded"] += 1
                l3_lines.append(f"[取代] {c}")
            continue
        try:
            tags = _L3_TAGS if it.get("is_l3") else ("蒸馏," + day)
            mem.add(it["kind"], c, tags)
            stat["added"] += 1
            if it.get("is_l3"):
                stat["l3"] += 1
                l3_lines.append(c)
        except Exception:
            continue

    # L3 思维层 → WorkBuddy 的 MEMORY.md
    if write_wb and l3_lines:
        stat["wb_lines"] = _write_wb(day, l3_lines)
    return stat


def _write_wb(day: str, lines: list[str]) -> int:
    """把思维层内容追加到 WorkBuddy 工作区 MEMORY.md（小智 ↔ WorkBuddy 的共享面）。"""
    wdir = _wb_memory_dir()
    if not wdir:
        return 0
    f = wdir / "MEMORY.md"
    try:
        block = ["", f"## 来自小智的思维层（{day} 自动蒸馏）", ""]
        block += [f"- {ln}" for ln in lines]
        if f.exists():
            old = f.read_text(encoding="utf-8")
            # 幂等：同一天的块不重复写
            if f"（{day} 自动蒸馏）" in old:
                return 0
            f.write_text(old.rstrip() + "\n" + "\n".join(block) + "\n", encoding="utf-8")
        else:
            f.write_text("\n".join(block) + "\n", encoding="utf-8")
        return len(lines)
    except Exception:
        return 0


# ---------------------------------------------------------------- 调度入口

def run_once(day: str = None, force: bool = False) -> dict:
    """跑一次蒸馏。默认只处理"还没处理过"的日期，幂等。"""
    day = day or datetime.now().strftime("%Y-%m-%d")
    with LOCK:
        st = _state()
        if not force and day in st.get("done", {}):
            return {"ok": True, "day": day, "skipped": True,
                    "reason": "该日期已蒸馏过（force=True 可强制重跑）"}
        mat = collect_today(day)
        items = distill(mat)
        degraded = False
        if not items:
            # 降级：至少把当天的对话原样留住，不丢东西
            degraded = True
            items = [{"content": m["content"][:200], "kind": m["kind"],
                      "importance": 2, "is_l3": False}
                     for m in mat.get("memories", [])[:5]]
        stat = commit(items, day)
        st.setdefault("done", {})[day] = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "candidates": len(mat.get("memories", [])) + len(mat.get("tasks", [])),
            "extracted": len(items), "degraded": degraded, **stat,
        }
        _save_state(st)
        return {"ok": True, "day": day, "degraded": degraded,
                "candidates": st["done"][day]["candidates"], **stat}


def status() -> dict:
    st = _state()
    return {
        "state_file": str(STATE),
        "done_days": sorted(st.get("done", {}).keys())[-14:],
        "total_days": len(st.get("done", {})),
        "last": list(st.get("done", {}).values())[-1] if st.get("done") else None,
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "collect":
        print(json.dumps(collect_today(), ensure_ascii=False, indent=2)[:2000])
    elif len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    else:
        d = sys.argv[1] if len(sys.argv) > 1 else None
        print(json.dumps(run_once(d, force=True), ensure_ascii=False, indent=2))
