#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory.py —— 本机记忆层（零重依赖、数据不出网）

设计取向：不装 chromadb / faiss 这类大件，用 SQLite + Ollama 本地嵌入就够了。
- 存储：SQLite（bridge/memory/memory.db），表 memories + FTS5 虚拟表
- 向量：本地 Ollama `nomic-embed-text`（768 维），余弦相似度
- 关键词：FTS5 trigram（对中文友好）
- 检索：向量 + 关键词混合打分，Ollama 挂了自动降级为纯关键词，绝不阻塞

kind 分类（对应主人勾的五类记忆）：
    chat      对话记忆（说过什么）
    profile   用户画像（习惯/偏好）
    project   项目上下文（在做什么、进度）
    knowledge 知识库（文档/台账要点）
    task      任务记忆（干过什么活、结果在哪）
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "memory" / "memory.db"
DB_PATH.parent.mkdir(exist_ok=True)

OLLAMA_URL = "http://127.0.0.1:11434/api/embeddings"
OLLAMA_HOST = "http://127.0.0.1:11434"
EMBED_TIMEOUT = 25

# 嵌入模型优先级：中文效果 bge-m3 >> nomic-embed-text（实测 nomic 的中文短句
# 相似度全挤在 0.5~0.8，区分度不够，会把"隐私偏好"排到"板子存储"前面）
_MODEL_PREFERENCE = ("bge-m3", "nomic-embed-text")
_MODEL_CACHE: str | None = None


def _model() -> str:
    """自动挑一个本机已装且效果最好的嵌入模型（可用 XZ_EMBED_MODEL 环境变量覆盖）"""
    global _MODEL_CACHE
    if _MODEL_CACHE:
        return _MODEL_CACHE
    forced = os.environ.get("XZ_EMBED_MODEL")
    if forced:
        _MODEL_CACHE = forced
        return forced
    try:
        with urllib.request.urlopen(OLLAMA_HOST + "/api/tags", timeout=3) as r:
            names = [m["name"] for m in json.loads(r.read()).get("models", [])]
        for pref in _MODEL_PREFERENCE:
            for n in names:
                if n.split(":")[0] == pref:
                    _MODEL_CACHE = n
                    return n
        if names:
            _MODEL_CACHE = names[0]
            return names[0]
    except Exception:
        pass
    _MODEL_CACHE = "nomic-embed-text"
    return _MODEL_CACHE

KINDS = ("chat", "profile", "project", "knowledge", "task")


# ---------------------------------------------------------------- 嵌入
def embed(text: str, retry: int = 1) -> list[float] | None:
    """本地 Ollama 生成嵌入；任何失败返回 None（上层自动降级）

    坑记录：nomic-embed-text 首次调用要冷启动加载模型，实测能到 10~20 秒，
    8 秒超时会静默失败 → 表现为"有的记忆有向量、有的没有"。
    所以这里超时放到 25 秒，并带一次重试；另提供 warmup() 在桥启动时预热。
    keep_alive 让模型常驻内存，后续调用降到 1 秒内。
    """
    if not text.strip():
        return None
    body = json.dumps({"model": _model(), "prompt": text,
                       "keep_alive": "10m"}).encode("utf-8")
    for attempt in range(retry + 1):
        req = urllib.request.Request(OLLAMA_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT) as r:
                return json.loads(r.read()).get("embedding")
        except Exception:
            if attempt >= retry:
                return None
    return None


def warmup() -> bool:
    """预热：让 Ollama 把嵌入模型装进内存，避免第一次说话时卡十几秒"""
    return embed("预热") is not None


def _kw_score(query: str, text: str) -> float:
    """中文关键词打分：FTS5 trigram 对整句短语会漏召回，
    所以这里直接用 2-gram 重合率，'固件升级'能命中'固件 2.0.1'"""
    q = query.strip()
    if not q or not text:
        return 0.0
    grams = [q[i:i + 2] for i in range(len(q) - 1)] or [q]
    hit = sum(1 for g in grams if g in text) / len(grams)
    chars = len(set(q) & set(text)) / max(1, len(set(q)))
    return max(hit * 0.55, chars * 0.30)


def _pack(v: list[float] | None) -> bytes | None:
    if not v:
        return None
    return json.dumps(v).encode("utf-8")


def _unpack(b) -> list[float] | None:
    if not b:
        return None
    try:
        return json.loads(b)
    except Exception:
        return None


def _cos(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------- 库
def connect() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB_PATH))
    c.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT DEFAULT '',
            embed BLOB
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind)")
    try:
        c.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(content, tags, tokenize='trigram')
        """)
    except sqlite3.OperationalError:
        pass
    c.commit()
    return c


def add(kind: str, content: str, tags: str = "") -> int:
    kind = kind if kind in KINDS else "chat"
    c = connect()
    cur = c.execute(
        "INSERT INTO memories(ts, kind, content, tags, embed) VALUES(?,?,?,?,?)",
        (datetime.now().isoformat(timespec="seconds"), kind, content.strip(), tags, _pack(embed(content))),
    )
    mid = cur.lastrowid
    try:
        c.execute("INSERT INTO memories_fts(rowid, content, tags) VALUES(?,?,?)", (mid, content.strip(), tags))
    except sqlite3.OperationalError:
        pass
    c.commit()
    c.close()
    return mid


def search(query: str, limit: int = 5, kind: str | None = None) -> list[dict]:
    """混合检索：向量相似度为主，关键词命中为辅，最近写入轻微加权"""
    c = connect()
    qv = embed(query)
    rows = c.execute(
        "SELECT id, ts, kind, content, tags, embed FROM memories"
        + (" WHERE kind=?" if kind else ""),
        (kind,) if kind else (),
    ).fetchall()

    # 关键词兜底分（逐条算 2-gram 重合，中文比 FTS5 trigram 整句匹配靠谱）
    kw_hit: dict[int, float] = {
        rid: _kw_score(query, content) for rid, content in
        c.execute("SELECT id, content FROM memories" + (" WHERE kind=?" if kind else ""),
                  (kind,) if kind else ()).fetchall()
    }
    kw_hit = {k: v for k, v in kw_hit.items() if v > 0.05}

    scored = []
    for rid, ts, k, content, tags, eb in rows:
        s = kw_hit.get(rid, 0.0)
        if qv:
            v = _unpack(eb)
            if v:
                s = max(s, _cos(qv, v))          # 向量分优先，关键词只兜底
        if s <= 0.08:
            continue
        scored.append({"id": rid, "ts": ts, "kind": k, "content": content, "tags": tags, "score": round(s, 3)})
    scored.sort(key=lambda x: -x["score"])
    c.close()
    return scored[:limit]


def recent(limit: int = 10, kind: str | None = None) -> list[dict]:
    c = connect()
    rows = c.execute(
        "SELECT id, ts, kind, content, tags FROM memories"
        + (" WHERE kind=?" if kind else "") + " ORDER BY id DESC LIMIT ?",
        ((kind, limit) if kind else (limit,)),
    ).fetchall()
    c.close()
    return [{"id": r[0], "ts": r[1], "kind": r[2], "content": r[3], "tags": r[4]} for r in rows]


def delete(mid: int) -> bool:
    c = connect()
    c.execute("DELETE FROM memories WHERE id=?", (mid,))
    try:
        c.execute("DELETE FROM memories_fts WHERE rowid=?", (mid,))
    except sqlite3.OperationalError:
        pass
    c.commit()
    n = c.total_changes
    c.close()
    return n > 0


def reindex() -> int:
    """换嵌入模型后必须重建向量——不同模型的向量空间不通用，
    混着用等于把两种尺子的读数放一起比大小"""
    c = connect()
    rows = c.execute("SELECT id, content FROM memories").fetchall()
    n = 0
    for rid, content in rows:
        v = embed(content)
        if v:
            c.execute("UPDATE memories SET embed=? WHERE id=?", (_pack(v), rid))
            n += 1
    c.commit()
    c.close()
    return n


def backfill() -> int:
    """把缺失向量的条目补上（Ollama 冷启动超时会导致漏网）"""
    c = connect()
    rows = c.execute("SELECT id, content FROM memories WHERE embed IS NULL").fetchall()
    c.close()
    n = 0
    for rid, content in rows:
        v = embed(content)
        if v:
            c = connect()
            c.execute("UPDATE memories SET embed=? WHERE id=?", (_pack(v), rid))
            c.commit()
            c.close()
            n += 1
    return n


def stats() -> dict:
    c = connect()
    total = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    by_kind = dict(c.execute("SELECT kind, COUNT(*) FROM memories GROUP BY kind").fetchall())
    with_vec = c.execute("SELECT COUNT(*) FROM memories WHERE embed IS NOT NULL").fetchone()[0]
    c.close()
    return {"total": total, "by_kind": by_kind, "with_vector": with_vec, "db": str(DB_PATH)}


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "recent"
    if cmd == "stats":
        print(json.dumps({"model": _model(), **stats()}, ensure_ascii=False, indent=2))
    elif cmd == "search":
        for r in search(" ".join(sys.argv[2:])):
            print(f"[{r['score']}] ({r['kind']}) {r['content'][:80]}")
    elif cmd == "reindex":
        print("重建向量条数：", reindex())
    elif cmd == "backfill":
        print("补向量条数：", backfill())
    elif cmd == "warmup":
        print("预热：", warmup(), "模型", _model())
    else:
        for r in recent(10):
            print(f"#{r['id']} ({r['kind']}) {r['ts']} {r['content'][:80]}")
