#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
todo_tools.py —— 「未完成事项」工具集（主人的最终目标：主动关注我没做完的事）

为什么不用 memory_tools 的 remember？
    memories 是"知识条目"，append-only；待办有生命周期（pending→done）、
    有截止时间、有"提醒过没有"，塞进去语义会搅乱。
    所以另建 bridge/todo.py 单独一张 todos 表，本文件只负责把它暴露给 LLM。

降级原则与 memory_tools 一致：**绝不阻塞语音**。模块加载失败或 SQLite 报错，
一律返回一句可朗读的中文，绝不抛异常。

工具：
    add_todo      记一件事（支持"明天下午三点""下周一""三天后"这类自然语言时间）
    list_todos    查未完成 / 已完成
    done_todo     标记完成
    todo_digest   今日 + 逾期汇总（主人问"我今天有什么事""有什么没做完的"时用）
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

_PROJECT_ROOT = Path(__file__).resolve().parents[4]

_TODO = None
_TODO_ERR = ""


def _todo():
    """懒加载 bridge/todo.py；失败返回 None。"""
    global _TODO, _TODO_ERR
    if _TODO is not None:
        return _TODO
    try:
        env = os.environ.get("XZ_BRIDGE_DIR")
        base = Path(env) if env else (_PROJECT_ROOT / "bridge")
        path = base / "todo.py"
        if not path.exists():
            _TODO_ERR = f"未找到待办模块 {path}"
            return None
        spec = importlib.util.spec_from_file_location("xz_bridge_todo", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _TODO = mod
        logger.bind(tag=TAG).info(f"待办模块已加载: {path}")
        return mod
    except Exception as e:
        _TODO_ERR = str(e)
        logger.bind(tag=TAG).error(f"待办模块加载失败: {e}")
        return None


def _fmt(t: dict) -> str:
    """把一条 todo 拼成人能听的短句。"""
    title = t.get("title", "")
    due = (t.get("due") or "").strip()
    if not due:
        return f"· {title}（没定时间）"
    day, _, hm = due.partition(" ")
    today = _today()
    if day == today:
        rel = "今天" + (f" {hm}" if hm else "")
    else:
        try:
            from datetime import datetime, timedelta
            d = datetime.strptime(day, "%Y-%m-%d").date()
            diff = (d - datetime.now().date()).days
            if diff == 1:
                rel = "明天" + (f" {hm}" if hm else "")
            elif diff == 2:
                rel = "后天" + (f" {hm}" if hm else "")
            elif diff < 0:
                rel = f"已过期 {-diff} 天"
            else:
                rel = due
        except Exception:
            rel = due
    return f"· {title}（{rel}）"


def _today() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 记一件事
add_todo_desc = {
    "type": "function",
    "function": {
        "name": "add_todo",
        "description": (
            "记下一件主人还没做完的事，之后小智会主动盯着它、到点主动提醒。"
            "当主人说「提醒我…」「我得…」「别忘了…」「记一下要…」「改天要…」时调用。"
            "已经做完的事不要记进来。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "要做的事，简洁一句话。例：交房租、给爸妈回电话、修复登录 bug。",
                },
                "due": {
                    "type": "string",
                    "description": (
                        "什么时候做。支持自然语言：今天、明天、后天、明天下午三点、"
                        "今晚八点、下周一、三天后、10月5日、2026-10-05 08:30。"
                        "没有时间就传空字符串。"
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "补充说明，可选。",
                },
            },
            "required": ["title"],
        },
    },
}


@register_function("add_todo", add_todo_desc, ToolType.WAIT)
def add_todo(title: str, due: str = "", note: str = ""):
    """记一件未完成的事。"""
    title = (title or "").strip()
    if not title:
        return ActionResponse(Action.REQLLM, "主人没说要做的事是什么，请追问一句。", None)
    td = _todo()
    if td is None:
        return ActionResponse(Action.REQLLM, f"待办功能不可用（{_TODO_ERR}）。", None)
    try:
        mid = td.add(title, due or "", note or "")
    except Exception as e:
        logger.bind(tag=TAG).error(f"add_todo 失败: {e}")
        return ActionResponse(Action.REQLLM, f"记下来失败了（{e}）。", None)
    if not mid:
        return ActionResponse(Action.REQLLM, "这件事没记上，请再说一遍。", None)

    when = td.normalize_due(due) if due else ""
    if when:
        return ActionResponse(
            Action.REQLLM,
            f"记下了：{title}，{when}。到时候我会主动提醒你。",
            None,
        )
    return ActionResponse(
        Action.REQLLM,
        f"记下了：{title}。你没说什么时候做，我会一直挂着，你想起来问我我就报。",
        None,
    )


# ---------------------------------------------------------------- 查
list_todos_desc = {
    "type": "function",
    "function": {
        "name": "list_todos",
        "description": (
            "列出主人还没做完的事。当主人问「我还有什么没做」「还有哪些事没完成」"
            "「看看我的待办」时调用。传 status='done' 可查已完成的。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "done", "dropped", "all"],
                    "description": "默认 pending（未完成）。",
                },
                "limit": {"type": "integer", "description": "返回条数，默认 10，最多 30。"},
            },
            "required": [],
        },
    },
}


@register_function("list_todos", list_todos_desc, ToolType.WAIT)
def list_todos(status: str = "pending", limit: int = 10):
    """列待办。"""
    td = _todo()
    if td is None:
        return ActionResponse(Action.REQLLM, f"待办功能不可用（{_TODO_ERR}）。", None)
    try:
        limit = max(1, min(int(limit or 10), 30))
    except Exception:
        limit = 10
    try:
        rows = td.list_todos((status or "pending"), limit=limit)
    except Exception as e:
        logger.bind(tag=TAG).error(f"list_todos 失败: {e}")
        return ActionResponse(Action.REQLLM, f"查待办出错了（{e}）。", None)
    if not rows:
        label = {"pending": "未完成", "done": "已完成", "dropped": "已放弃"}.get(status, "待办")
        return ActionResponse(Action.REQLLM, f"主人的{label}事项是空的。", None)
    lines = [_fmt(r) for r in rows]
    return ActionResponse(
        Action.REQLLM,
        f"共 {len(rows)} 条：\n" + "\n".join(lines) + "\n请如实转达，不要自己加戏。",
        None,
    )


# ---------------------------------------------------------------- 完成
done_todo_desc = {
    "type": "function",
    "function": {
        "name": "done_todo",
        "description": (
            "把一件待办标记为已完成。当主人说「这件事做完了」「房租交了」「搞定了」"
            "并且上下文能对上某条待办时调用。只在主人明确说做完了时才调用，不要自作主张。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "要标记完成的那件事的标题（与 add_todo 时写的一致，或其中一部分）。",
                },
            },
            "required": ["title"],
        },
    },
}


@register_function("done_todo", done_todo_desc, ToolType.WAIT)
def done_todo(title: str):
    """标记完成。"""
    title = (title or "").strip()
    td = _todo()
    if td is None:
        return ActionResponse(Action.REQLLM, f"待办功能不可用（{_TODO_ERR}）。", None)
    if not title:
        return ActionResponse(Action.REQLLM, "哪件事做完了？", None)
    try:
        pend = td.list_todos("pending", 50)
        # 先精确匹配，再包含匹配
        hit = next((t for t in pend if t.get("title") == title), None)
        if hit is None:
            hit = next((t for t in pend if title and (title in t.get("title", "")
                                                       or t.get("title", "") in title)), None)
        if hit is None:
            names = "、".join(t.get("title", "") for t in pend[:6])
            return ActionResponse(
                Action.REQLLM,
                f"没找到叫「{title}」的待办。主人现在挂着的是：{names or '（空）'}。请确认一下。",
                None,
            )
        td.done(hit["id"])
        return ActionResponse(Action.REQLLM, f"好，{hit.get('title')} 标记为已完成。", None)
    except Exception as e:
        logger.bind(tag=TAG).error(f"done_todo 失败: {e}")
        return ActionResponse(Action.REQLLM, f"标记失败（{e}）。", None)


# ---------------------------------------------------------------- 汇总
todo_digest_desc = {
    "type": "function",
    "function": {
        "name": "todo_digest",
        "description": (
            "生成待办摘要：今天要做的 + 已经过期的 + 全部未完成。"
            "当主人问「我今天有什么安排」「今天要干什么」「有什么要紧事」"
            "或小智自己早上做主动播报时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "include_all": {
                    "type": "boolean",
                    "description": "true=连未来几天的也列进来；false（默认）只看今天和已过期。",
                }
            },
            "required": [],
        },
    },
}


@register_function("todo_digest", todo_digest_desc, ToolType.WAIT)
def todo_digest(include_all: bool = False):
    """今日 + 逾期摘要。"""
    td = _todo()
    if td is None:
        return ActionResponse(Action.REQLLM, f"待办功能不可用（{_TODO_ERR}）。", None)
    try:
        st = td.stats()
        if st["pending"] == 0:
            return ActionResponse(Action.REQLLM, "主人目前没有任何未完成的事项。", None)
        today = td.due_today()
        over = td.overdue()
        parts = []
        if over:
            parts.append("已经过期还没做的：\n" + "\n".join(_fmt(t) for t in over))
        if today:
            parts.append("今天要做的：\n" + "\n".join(_fmt(t) for t in today))
        if include_all or not parts:
            rest = [t for t in td.list_todos("pending", 30)
                    if t not in today and t not in over]
            if rest:
                parts.append("还没定时间的：\n" + "\n".join(_fmt(t) for t in rest[:8]))
        head = f"主人目前有 {st['pending']} 件未完成"
        if st["overdue"]:
            head += f"，其中 {st['overdue']} 件已经过期"
        head += "。"
        return ActionResponse(Action.REQLLM, head + "\n" + "\n".join(parts), None)
    except Exception as e:
        logger.bind(tag=TAG).error(f"todo_digest 失败: {e}")
        return ActionResponse(Action.REQLLM, f"汇总出错了（{e}）。", None)
