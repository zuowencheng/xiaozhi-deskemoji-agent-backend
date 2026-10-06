#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""archive_client.py —— 个人知识库只读客户端（**预留桩**）

状态
    预留（RESERVED）。**尚未注册进 server_agent.py 的 MCP 工具列表** —— 现在挂上去
    等于给还在冲刺的最小闭环增加变量。等触发条件满足再包成 `search_my_archive` 工具。
    触发条件见 docs/13-与个人知识库打通预留.md  §一。

对面契约
    C:\\Orcan\\02_个人知识资产\\知识库对外接口契约_v1.md
    服务：http://127.0.0.1:8765（本机只读 HTTP，由用户双击 start_kb_server.cmd 启动）

设计约束（与 memory.py 的降级策略保持一致）
    - 只读，绝不写知识库
    - 短超时（默认 6 秒）；任何失败返回空值，**绝不抛异常打断语音链路**
    - **不读知识库的向量**（那边是 nomic-embed-text，本侧优选 bge-m3，向量空间不可比）
      只走「文本桥」：拿回文本，要不要算向量由本侧用自己的模型决定
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

KB_BASE = os.environ.get("KB_BASE", "http://127.0.0.1:8765")
TIMEOUT = float(os.environ.get("KB_TIMEOUT", "6"))


def _get(path: str, params: dict | None = None, timeout: float = TIMEOUT):
    url = KB_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def health(timeout: float = TIMEOUT) -> dict | None:
    """知识库是否在线。在线返回 dict，不在线返回 None。"""
    d = _get("/api/health", timeout=timeout)
    return d if (d and d.get("ok")) else None


def search(query: str, mode: str = "hybrid", top: int = 5,
           timeout: float = TIMEOUT) -> list[dict]:
    """检索 20 年资料。返回 list[dict]；服务不在线或出错返回 []。

    mode: hybrid 混合 / fulltext 全文 / semantic 语义
    """
    if not query or not query.strip():
        return []
    d = _get("/api/search", {"q": query, "mode": mode, "top": top}, timeout)
    return (d or {}).get("results", [])


def fetch_doc(path: str, max_chars: int = 2000,
              timeout: float = TIMEOUT) -> dict | None:
    """取某份文档的正文（只认 /api/search 返回过的 path，防目录穿越）。"""
    if not path:
        return None
    d = _get("/api/doc", {"path": path, "max": max_chars}, timeout)
    return d if (d and d.get("ok")) else None


def speakable(query: str, top: int = 3, snip: int = 120) -> str:
    """给语音用的一行一条口语摘要。查不到返回空串（调用方据此说"没找到"）。"""
    hits = search(query, top=top)
    if not hits:
        return ""
    lines = []
    for h in hits:
        s = (h.get("snippet") or "").strip().replace("\n", " ")
        lines.append(f"{h.get('name', '')}（{h.get('mtime', '')}）：{s[:snip]}")
    return "\n".join(lines)


def _argv_text(s: str) -> str:
    """修 Windows 下经某些 shell 传参导致的中文乱码。

    现象：Git Bash 把 UTF-8 中文传给 python.exe 时会被按 GBK 误解码
    （"重庆" → "閲嶅簡"），结果查询永远查不到东西。
    做法：尝试用 gbk 还原原始字节再按 utf-8 解；解不通就原样返回（正常中文走这条）。
    MCP 工具调用走函数、不走命令行，所以**不影响真实链路**，纯粹为自测方便。
    """
    for enc in ("gbk", "utf-8"):
        try:
            return s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return s


if __name__ == "__main__":
    import sys

    argv = [_argv_text(a) for a in sys.argv]
    cmd = argv[1] if len(argv) > 1 else "health"
    if cmd == "health":
        h = health()
        print(json.dumps(h, ensure_ascii=False, indent=2) if h
              else "知识库不在线（先双击启动 start_kb_server.cmd）")
    elif cmd == "search":
        rs = search(" ".join(argv[2:]))
        if not rs:
            print("没有命中，或知识库不在线")
        for h in rs:
            print(f"[{h['score']:.3f}] {h['name']}")
            print(f"        {h['path']}")
    elif cmd == "doc":
        d = fetch_doc(argv[2]) if len(argv) > 2 else None
        print(d["text"][:1500] if d else "取不到（路径未索引 / 服务不在线）")
    else:
        print("用法: archive_client.py [health | search 查询词 | doc 路径]")
