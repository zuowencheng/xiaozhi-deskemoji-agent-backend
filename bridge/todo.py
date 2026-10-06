#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
todo.py —— 未完成事项跟踪（主人的最终目标：主动关注我没做完的事，并主动提醒）

为什么单独一个文件、而不是往 memories 表里塞？
    memories 是"知识条目"，append-only，写进去就不管了；
    todo 有生命周期（pending → done/dropped）、有截止时间、有"提醒过没有"，
    塞进 memories 会把这两个语义搅在一起，查询也没法用索引。

存储：和 memory.py 共用同一个 SQLite 文件（bridge/memory/memory.db），不新增文件。
零外部依赖，只用标准库 sqlite3 —— 语音链路最怕的就是某个依赖挂了把对话卡死。

对外 API（全部纯函数式，失败返回 None/[]，绝不抛异常）：
    add(title, due="", note="")   记一件事
    list_todos(status="pending")  查（默认未完成）
    get(mid)                      看详情
    done(mid) / reopen(mid)       改状态
    drop(mid)                     放弃
    due_today(on_day=None)        当天到期且仍 pending（巡检主力）
    overdue(on_day=None)          已过期还没做（主人最该被提醒的）
    mark_reminded(mid)            打"已提醒"戳，避免每次巡检都重复念
    stats()                       给页面看的统计
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "memory" / "memory.db"

STATUSES = ("pending", "done", "dropped")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    note        TEXT DEFAULT '',
    due         TEXT DEFAULT '',
    priority    INTEGER DEFAULT 2,
    status      TEXT DEFAULT 'pending',
    created_at  TEXT NOT NULL,
    done_at     TEXT,
    reminded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status, due);
"""

# 中文/英文时间表达 → 标准日期。刻意保守：认不准就不猜，宁可当"无期限"也别误报。
CN_NUM = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _parse_cn_num(s: str) -> int | None:
    """解析 '三' / '十' / '十五' / '二十' 这类中文数字（≤99，够用）。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    if not s:
        return None
    if s == "十":
        return 10
    m = re.match(r"^([一二两三四五六七八九])?十([一二三四五六七八九])?$", s)
    if m:
        tens = CN_NUM.get(m.group(1), 1)
        ones = CN_NUM.get(m.group(2), 0)
        return tens * 10 + ones
    m = re.match(r"^([二两三四五六七八九十]+)$", s)
    if m and "十" not in s:
        v = 0
        for ch in s:
            if ch not in CN_NUM:
                return None
            v = v * 10 + CN_NUM[ch]
        return v
    return None


def normalize_due(text: str, now: datetime | None = None) -> str:
    """把自然语言时间规整成 'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM'。

    认得的写法：
        今天 / 明天 / 后天 / 大后天
        3天后 / 三天后 / 两小时后
        下周一..下周日 / 周一..周日
        10月5日 / 10-05 / 2026-10-05 / 2026/10/05 08:30
        明早八点 / 今晚八点（→ 早 08:00 / 晚 20:00）
    认不出的返回 ''（表示"没期限"，不会误报）。
    """
    now = now or datetime.now()
    if not text:
        return ""
    t = str(text).strip()
    if not t:
        return ""

    # 直接能被 datetime 解析的（ISO 等）
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d %H:%M" if "%H" in fmt else "%Y-%m-%d")
        except ValueError:
            pass

    # ---------- 第一步：抽日期 ----------
    day = None
    m = re.search(r"(今天|明天|后天|大后天|今早|明早|今晚|明晚)", t)
    if m:
        w = m.group(1)
        day = now + timedelta(days={"今天": 0, "今早": 0, "今晚": 0,
                                    "明天": 1, "明早": 1, "明晚": 1,
                                    "后天": 2, "大后天": 3}[w])
    if day is None:
        m = re.search(r"(\d+|[零一二两三四五六七八九十]+)\s*[天周]\s*[后後]", t)
        if m:
            n = _parse_cn_num(m.group(1))
            if n is not None:
                day = now + timedelta(days=n * (7 if "周" in m.group(0) else 1))
    if day is None:
        m = re.search(r"(\d+|[零一二两三四五六七八九十]+)\s*个?月\s*(\d+|[零一二两三四五六七八九十]+)\s*[日号]?", t)
        if m:
            mo, dd = _parse_cn_num(m.group(1)), _parse_cn_num(m.group(2))
            if mo and dd and 1 <= mo <= 12 and 1 <= dd <= 31:
                try:
                    cand = datetime(now.year, mo, dd)
                except ValueError:
                    cand = None
                if cand:
                    if cand < now - timedelta(days=30):
                        cand = datetime(cand.year + 1, mo, dd)
                    day = cand
    if day is None:
        m = re.search(r"(下|这|本)?\s*(周|星期)([一二三四五六日天])", t)
        if m:
            want = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}[m.group(3)]
            ahead = (want - now.weekday()) % 7
            if m.group(1) == "下":
                ahead = ahead or 7
            day = now + timedelta(days=ahead)
    if day is None:
        # "什么时候/啥时候/改天/有空"= 没有期限，别硬猜成今天
        if re.search(r"什么时候|啥时候|何时候|改天|有空|再说|不急|随时", t):
            return ""
        # 只说了时刻（"下午三点"）或只说了"早上/晚上" → 默认今天
        if re.search(r"[点:：]", t) or re.search(r"早上|早晨|上午|中午|下午|傍晚|晚上|夜里", t):
            day = now
        else:
            return ""      # 真认不出 -> 当没期限，宁可漏报不误报

    # ---------- 第二步：抽时刻 ----------
    hh = None
    mm = 0
    m = re.search(r"(早上|早晨|上午|中午|下午|傍晚|晚上|夜里|今晚)?\s*([零一二两三四五六七八九十]+|\d{1,2})\s*[点:：时]", t)
    if m:
        word = m.group(1) or ""
        n = _parse_cn_num(m.group(2))
        if n is not None and 0 <= n <= 23:
            hh = n
            if n < 12 and any(w in word for w in ("下午", "傍晚", "晚上", "夜里", "今晚", "明晚")):
                hh = n + 12          # "下午三点" -> 15, "今晚八点" -> 20
            m2 = re.search(r"[点:：时]\s*(\d{1,2})", t)
            if m2:
                mm = int(m2.group(1))
    if hh is None:
        # "明早"/"今晚"这种没带数字的，用习惯时刻兜底
        if re.search(r"今晚|晚上|夜里", t):
            hh = 20
        elif re.search(r"明早|早上|早晨|上午", t):
            hh = 8
        elif re.search(r"中午", t):
            hh = 12
        elif re.search(r"下午|傍晚", t):
            hh = 15

    out = day.strftime("%Y-%m-%d")
    if hh is not None:
        out += " %02d:%02d" % (hh, mm if 0 <= mm <= 59 else 0)
    return out


def _connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=3.0)
    c.row_factory = sqlite3.Row
    c.executescript(_SCHEMA)
    return c


# ---------------- 写入 ----------------

def add(title: str, due: str = "", note: str = "", priority: int = 2) -> int | None:
    """记一件未完成的事。title 为空返回 None。"""
    title = (title or "").strip()
    if not title:
        return None
    try:
        d = normalize_due(due) if due else ""
        c = _connect()
        cur = c.execute(
            "INSERT INTO todos(title, note, due, priority, status, created_at)"
            " VALUES(?,?,?,?,'pending',?)",
            (title, (note or "").strip(), d, int(priority or 2),
             datetime.now().isoformat(timespec="seconds")),
        )
        c.commit()
        mid = cur.lastrowid
        c.close()
        return mid
    except Exception:
        return None


def done(mid: int) -> bool:
    try:
        c = _connect()
        c.execute("UPDATE todos SET status='done', done_at=? WHERE id=?",
                  (datetime.now().isoformat(timespec="seconds"), int(mid)))
        c.commit()
        c.close()
        return True
    except Exception:
        return False


def reopen(mid: int) -> bool:
    try:
        c = _connect()
        c.execute("UPDATE todos SET status='pending', done_at=NULL WHERE id=?", (int(mid),))
        c.commit()
        c.close()
        return True
    except Exception:
        return False


def drop(mid: int) -> bool:
    try:
        c = _connect()
        c.execute("UPDATE todos SET status='dropped', done_at=? WHERE id=?",
                  (datetime.now().isoformat(timespec="seconds"), int(mid)))
        c.commit()
        c.close()
        return True
    except Exception:
        return False


def mark_reminded(mid: int) -> bool:
    try:
        c = _connect()
        c.execute("UPDATE todos SET reminded_at=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), int(mid)))
        c.commit()
        c.close()
        return True
    except Exception:
        return False


# ---------------- 读取 ----------------

def get(mid: int) -> dict | None:
    try:
        c = _connect()
        r = c.execute("SELECT * FROM todos WHERE id=?", (int(mid),)).fetchone()
        c.close()
        return dict(r) if r else None
    except Exception:
        return None


def list_todos(status: str = "pending", limit: int = 20) -> list[dict]:
    try:
        c = _connect()
        if status and status != "all":
            rows = c.execute(
                "SELECT * FROM todos WHERE status=? ORDER BY "
                "CASE WHEN due='' THEN 1 ELSE 0 END, due ASC, id DESC LIMIT ?",
                (status, int(limit))).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM todos ORDER BY "
                "CASE WHEN due='' THEN 1 ELSE 0 END, due ASC, id DESC LIMIT ?",
                (int(limit),)).fetchall()
        c.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _day_of(due: str) -> str:
    return (due or "")[:10]


def due_today(on_day: str | None = None) -> list[dict]:
    target = on_day or datetime.now().strftime("%Y-%m-%d")
    return [t for t in list_todos("pending", 50) if _day_of(t.get("due")) == target]


def overdue(on_day: str | None = None) -> list[dict]:
    """已过期但还没做 —— 主人最该被提醒的（可以很多天前就欠着了）。"""
    target = on_day or datetime.now().strftime("%Y-%m-%d")
    out = []
    for t in list_todos("pending", 100):
        d = _day_of(t.get("due"))
        if d and d < target:
            out.append(t)
    return out


def needs_remind(within_hours: int = 12) -> list[dict]:
    """巡检主力：已经到期/快到期/早该做但没做，且**最近没提醒过**（4 小时内不重复念）。"""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    soon = (now + timedelta(hours=within_hours)).strftime("%Y-%m-%d %H:%M")
    out = []
    for t in list_todos("pending", 100):
        due = t.get("due") or ""
        if not due:
            continue
        ra = t.get("reminded_at")
        if ra:
            try:
                if (now - datetime.fromisoformat(ra)).total_seconds() < 4 * 3600:
                    continue          # 4 小时内提醒过就不念了
            except Exception:
                pass
        if due <= soon:
            out.append(t)
    return out


def stats() -> dict:
    try:
        c = _connect()
        row = c.execute("SELECT status, COUNT(*) n FROM todos GROUP BY status").fetchall()
        c.close()
        d = {r["status"]: r["n"] for r in row}
        return {
            "pending": d.get("pending", 0),
            "done": d.get("done", 0),
            "dropped": d.get("dropped", 0),
            "overdue": len(overdue()),
            "due_today": len(due_today()),
        }
    except Exception:
        return {"pending": 0, "done": 0, "dropped": 0, "overdue": 0, "due_today": 0}


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "add":
        mid = add(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
        print(json.dumps({"id": mid}, ensure_ascii=False))
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        print(json.dumps(list_todos(sys.argv[2] if len(sys.argv) > 2 else "pending"),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"stats": stats(), "need_remind": needs_remind()},
                         ensure_ascii=False, indent=2))
