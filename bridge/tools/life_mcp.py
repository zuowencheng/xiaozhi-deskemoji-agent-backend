# -*- coding: utf-8 -*-
"""
life_mcp · 生活助理 MCP Server（能力编号 23~35）
==================================================

涵盖：M5 记忆 / M6 待办 / M7 音乐 / M8 联网搜索。

★ 与插件版的关系（重要）
------------------------
这四个模块原来都有**插件版**在服役：
    memory_tools.py      → remember / recall / list_memories / forget（**与 MCP 版同名**）
    todo_tools.py        → add_todo / list_todos / done_todo / todo_digest
    pc_music_control.py  → pc_open_music_app / pc_music_control
    web_search           → 插件版

同名工具同时存在会让模型分不清、也可能互相覆盖，所以**挂载本 Server 时必须
同步把 memory_tools / todo_tools / pc_music_control 从 `.config.yaml` 的
functions 列表里摘掉**（见 docs/67）。docs/63 阶段二原文：
「迁移完成后，从 plugins_func/functions/ 删掉对应插件」。

实现复用
--------
- 记忆   ← bridge/server_agent.py（同名工具，直接复用）
- 待办   ← bridge/todo.py（SQLite，与插件版**同一个库**，不会双写）
- 音乐   ← core/pc_music.py（系统媒体键）
- 搜索   ← core/pc_agent.tool_search_web（必应 HTML，免 key）

安全与纪律
----------
- stdout 是 JSON-RPC 协议流，任何 print 都会让握手失败 → 日志写 runtime/logs/life_mcp.log
- 每个工具包 try/except，单工具炸了不拖垮 Server
- docstring 必须是**普通字符串**（f-string 会让 description 变空串，模型乱选工具）

用法
----
    python bridge/tools/life_mcp.py --selftest   # 本地自检
    python bridge/tools/life_mcp.py              # stdio（由服务端拉起）
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve()
BRIDGE_DIR = _HERE.parents[1]
PROJECT_ROOT = _HERE.parents[2]
SERVER_DIR = PROJECT_ROOT / "server" / "xiaozhi-server"

for _p in (str(SERVER_DIR), str(BRIDGE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LOG_DIR = PROJECT_ROOT / "runtime" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "life_mcp.log"


def _log(msg: str) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass


_sa = None      # bridge/server_agent.py   -> 记忆
_todo = None    # bridge/todo.py           -> 待办
_pmusic = None  # core/pc_music.py         -> 音乐
_pa = None      # core/pc_agent.py         -> 搜索
_import_err: list[str] = []


def _init_impls() -> None:
    global _sa, _todo, _pmusic, _pa
    loads = (
        ("_sa", "server_agent(记忆)", lambda: __import__("server_agent")),
        ("_todo", "todo(待办)", lambda: __import__("todo")),
        ("_pmusic", "pc_music(音乐)", lambda: __import__("core.pc_music", fromlist=["x"])),
        ("_pa", "pc_agent(搜索)", lambda: __import__("core.pc_agent", fromlist=["x"])),
    )
    for slot, label, getter in loads:
        try:
            globals()[slot] = getter()
            _log(f"{label} 加载成功")
        except Exception as e:
            _import_err.append(f"{label}: {type(e).__name__}: {e}")
            _log(f"[ERR] {label} 加载失败 {type(e).__name__}: {e}\n{traceback.format_exc()}")


_init_impls()

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("deskemoji-life")


# ---------------------------------------------------------------------------
# M5 · 记忆（23 ~ 26）
# ---------------------------------------------------------------------------
@mcp.tool()
def remember(content: str, kind: str = "chat", tags: str = "") -> str:
    """把一条信息记进长期记忆，以后主人问起能想起来。主人告诉你任何偏好、习惯、事实、计划、项目进度、或刚说过的重要事情时，都要主动记下来，不要等主人提醒。kind 用这五种之一：chat（说过的话）、profile（主人的习惯偏好）、project（在做什么项目及进度）、knowledge（知识要点）、task（干过什么活）。tags 是可选关键词，用空格分隔。"""
    try:
        if _sa is None:
            return "[失败] 记忆模块没加载起来，看下日志。"
        return _sa.remember(content or "", kind or "chat", tags or "")
    except Exception as e:
        _log(f"[ERR] remember {type(e).__name__}: {e}")
        return f"[失败] 记忆写入出错：{type(e).__name__}"


@mcp.tool()
def recall(query: str, limit: int = 3) -> str:
    """从长期记忆里检索相关的旧事，支持语义模糊查找。主人问你记不记得什么、问过去说过的事、或要引用之前的信息时，先查记忆再回答。"""
    try:
        if _sa is None:
            return "[失败] 记忆模块没加载起来，看下日志。"
        return _sa.recall(query or "", int(limit or 3))
    except Exception as e:
        _log(f"[ERR] recall {type(e).__name__}: {e}")
        return f"[失败] 记忆检索出错：{type(e).__name__}"


@mcp.tool()
def list_memories(kind: str = "", limit: int = 8) -> str:
    """列出最近记住的内容，用于回答"你都记住了什么"。kind 留空看全部，也可填 chat / profile / project / knowledge / task 只看某一类。"""
    try:
        if _sa is None:
            return "[失败] 记忆模块没加载起来，看下日志。"
        return _sa.list_memories(kind or "", int(limit or 8))
    except Exception as e:
        _log(f"[ERR] list_memories {type(e).__name__}: {e}")
        return f"[失败] 列记忆出错：{type(e).__name__}"


@mcp.tool()
def forget(memory_id: int) -> str:
    """按编号删除一条记忆，编号可从 list_memories 查到。主人明确说"忘掉某件事"时才用。"""
    try:
        if _sa is None:
            return "[失败] 记忆模块没加载起来，看下日志。"
        return _sa.forget(int(memory_id))
    except Exception as e:
        _log(f"[ERR] forget {type(e).__name__}: {e}")
        return f"[失败] 删除记忆出错：{type(e).__name__}"


# ---------------------------------------------------------------------------
# M6 · 待办（27 ~ 30）
# ---------------------------------------------------------------------------
@mcp.tool()
def todo_add(title: str, due: str = "", note: str = "", priority: int = 2) -> str:
    """加一条待办事项。title 是事情本身；due 是截止时间，可以写"明天下午三点""周五""2026-10-10"这类中文或日期，会自动解析；priority 数字越小越优先（1最急/2普通/3不急）。"""
    try:
        if _todo is None:
            return "[失败] 待办模块没加载起来，看下日志。"
        mid = _todo.add(title or "", due or "", note or "", int(priority or 2))
        if not mid:
            return "[失败] 待办没加进去，看下日志。"
        tail = f"；截止 {due}" if due else ""
        return f"记下了（第 {mid} 条）：{title}{tail}"
    except Exception as e:
        _log(f"[ERR] todo_add {type(e).__name__}: {e}")
        return f"[失败] 加待办出错：{type(e).__name__}"


@mcp.tool()
def todo_list(status: str = "pending", limit: int = 20) -> str:
    """列待办。status 填 pending 看未完成的，done 看已完成的，all 看全部。主人问"我还有什么事没做""今天有什么安排"时用。"""
    try:
        if _todo is None:
            return "[失败] 待办模块没加载起来，看下日志。"
        rows = _todo.list_todos(status or "pending", int(limit or 20))
        if not rows:
            return "目前没有这类待办。"
        parts = []
        for r in rows:
            due = r.get("due") or ""
            parts.append(f"#{r['id']} {r['title']}" + (f"（{due}）" if due else ""))
        return f"共 {len(rows)} 条：" + "；".join(parts)
    except Exception as e:
        _log(f"[ERR] todo_list {type(e).__name__}: {e}")
        return f"[失败] 列待办出错：{type(e).__name__}"


@mcp.tool()
def todo_done(memory_id: int) -> str:
    """按编号把一条待办标记成已完成。编号从 todo_list 里能查到。"""
    try:
        if _todo is None:
            return "[失败] 待办模块没加载起来，看下日志。"
        ok = _todo.done(int(memory_id))
        return f"第 {memory_id} 条已完成。" if ok else f"没找到第 {memory_id} 条，或它已经完成了。"
    except Exception as e:
        _log(f"[ERR] todo_done {type(e).__name__}: {e}")
        return f"[失败] 标记完成出错：{type(e).__name__}"


@mcp.tool()
def todo_del(memory_id: int) -> str:
    """按编号删掉一条待办。只在主人明确说"删掉那条待办"时才用，不要替他删。"""
    try:
        if _todo is None:
            return "[失败] 待办模块没加载起来，看下日志。"
        ok = _todo.drop(int(memory_id))
        return f"已删除第 {memory_id} 条待办。" if ok else f"没找到第 {memory_id} 条待办。"
    except Exception as e:
        _log(f"[ERR] todo_del {type(e).__name__}: {e}")
        return f"[失败] 删除待办出错：{type(e).__name__}"


# ---------------------------------------------------------------------------
# M7 · 音乐（31 ~ 34）
# ---------------------------------------------------------------------------
@mcp.tool()
def music_open() -> str:
    """在电脑上打开「酷狗音乐」并切到最前面。只负责打开软件，不负责播具体歌曲，所以回复时别说"已经帮你播上了某首歌"。"""
    try:
        if _pmusic is None:
            return "[失败] 音乐模块没加载起来，看下日志。"
        # ⚠️ 底层函数叫 open_player()，不是 open_app() —— 写错会 AttributeError
        return _pmusic.open_player()
    except Exception as e:
        _log(f"[ERR] music_open {type(e).__name__}: {e}")
        return f"[失败] 打开音乐软件出错：{type(e).__name__}"


@mcp.tool()
def music_control(action: str) -> str:
    """控制正在播放的音乐，走系统媒体键。action 可填：播放 / 暂停 / 继续（三者等价，都是播放暂停切换）、下一首、上一首、停止。也可以用英文 play_pause / next / prev / stop。"""
    try:
        if _pmusic is None:
            return "[失败] 音乐模块没加载起来，看下日志。"
        return _pmusic.transport(action or "")
    except Exception as e:
        _log(f"[ERR] music_control {type(e).__name__}: {e}")
        return f"[失败] 音乐控制出错：{type(e).__name__}"


# ---------------------------------------------------------------------------
# M8 · 联网搜索（35）
# ---------------------------------------------------------------------------
@mcp.tool()
def search_web(query: str, max_results: int = 5) -> str:
    """联网搜索，免 API Key（走必应中国端点，本机实测可达）。主人问到你不确定的、需要最新信息的问题时用，比如新闻、价格、某个陌生概念。"""
    try:
        if _pa is None:
            return "[失败] 搜索模块没加载起来，看下日志。"
        return _pa.tool_search_web(query or "", int(max_results or 5))
    except Exception as e:
        _log(f"[ERR] search_web {type(e).__name__}: {e}")
        return f"[失败] 搜索出错：{type(e).__name__}"


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
async def _selftest() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import os

    print("=" * 64)
    print("life_mcp 自检")
    print("=" * 64)
    if _import_err:
        print("\n[!] 底层模块加载有告警：")
        for e in _import_err:
            print("    -", e)
    else:
        print("\n[OK] 四个底层模块都加载成功（记忆/待办/音乐/搜索）")

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(_HERE)],
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print(f"\n[工具总数] {len(tools.tools)}")
            for t in tools.tools:
                desc = (t.description or "").strip()
                flag = "OK " if desc else "!! 描述为空"
                print(f"  {flag} {t.name:<16} {desc[:40]}")
            empty = [t.name for t in tools.tools if not (t.description or "").strip()]
            if empty:
                print(f"\n[FAIL] 这些工具 description 为空：{empty}")
                return 1

            print("\n--- 实调用（只读）---")
            for call in (
                ("list_memories", {"limit": 3}),
                ("recall", {"query": "学习路线", "limit": 2}),
                ("todo_list", {"status": "pending", "limit": 5}),
            ):
                try:
                    res = await s.call_tool(call[0], call[1])
                    txt = "".join(getattr(c, "text", "") or "" for c in res.content)
                    print(f"  [OK] {call[0]:<16} -> {txt[:100]}")
                except Exception as e:
                    print(f"  [!!] {call[0]:<16} -> {type(e).__name__}: {e}")
            print("\n全部检查完成。")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import asyncio

        sys.exit(asyncio.run(_selftest()))
    mcp.run()
