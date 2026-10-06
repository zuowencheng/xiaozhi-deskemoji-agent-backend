# -*- coding: utf-8 -*-
"""
reminder · 语音设定定时提醒
==========================

"小智，明天早上八点提醒我交房租" → LLM 调 `set_reminder` → 落盘 →
到点由 `core/notify_plugin.py` 的提醒循环主动开口播报。

为什么单独一个模块
------------------
它复用 `core/notify_plugin` 的存储与时间解析（同一进程，直接 import），
不重复造轮子；注册到 config 里会被展开成 set/list/cancel 三个工具。

时间格式（务必让模型转成带日期或 HH:MM）
----------------------------------------
    "HH:MM"            -> 最近一次该时刻（已过则顺延到明天）；daily=true 则每天
    "YYYY-MM-DDTHH:MM" -> 绝对时间一次性
"""

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


def _np():
    """延迟导入，避免插件加载期就去碰后台核心模块。"""
    from core import notify_plugin as np

    return np


set_reminder_desc = {
    "type": "function",
    "function": {
        "name": "set_reminder",
        "description": (
            "为主人设置一条定时提醒，到点设备会主动开口提醒他。"
            "当主人说「提醒我……」「X 点叫我……」「每天 X 点提醒我……」时调用。"
            "⚠️ 你必须把口语时间换算成明确格式："
            "需要指定日期时用 YYYY-MM-DDTHH:MM（当前日期见上下文）；"
            "只想每天重复时用 HH:MM 并把 daily 设为 true。"
            "不要自己宣称已经设置好——以本工具返回为准。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "提醒内容，简短口语化，例如「该交房租了」「十点开会」。",
                },
                "at": {
                    "type": "string",
                    "description": (
                        "提醒时间。一次性用 YYYY-MM-DDTHH:MM（如 2026-10-03T08:00）；"
                        "每天重复用 HH:MM（如 08:00）并设 daily=true。"
                    ),
                },
                "daily": {
                    "type": "boolean",
                    "description": "是否每天重复。默认 false（一次性）。",
                },
            },
            "required": ["text", "at"],
        },
    },
}


@register_function("set_reminder", set_reminder_desc, ToolType.WAIT)
def set_reminder(text: str, at: str, daily: bool = False):
    from datetime import datetime

    text = (text or "").strip()
    at = (at or "").strip()
    if not text or not at:
        return ActionResponse(Action.REQLLM, "提醒内容和时间都得有，请再说一次。", None)

    try:
        np = _np()
        fire_at, is_clock = np._parse_at(at)
    except Exception as e:
        logger.bind(tag=TAG).error(f"提醒模块不可用: {e}")
        return ActionResponse(Action.REQLLM, f"提醒功能暂时不可用（{e}）。", None)

    if fire_at is None:
        return ActionResponse(
            Action.REQLLM,
            "时间没听懂，请让主人说得具体点，比如「明天早上八点」或「每天九点」。",
            None,
        )

    daily = bool(daily) or is_clock
    items = np._load_reminders()
    rid = np._next_id(items)
    items.append(
        {
            "id": rid,
            "text": text,
            "at": at,
            "daily": daily,
            "enabled": True,
            "created": datetime.now().isoformat(timespec="seconds"),
            "next_at": fire_at.isoformat(),
        }
    )
    np._save_reminders(items)
    logger.bind(tag=TAG).info(f"[提醒] 语音新增 {rid}: {text} @ {at} daily={daily}")
    when = fire_at.strftime("%m月%d日 %H:%M") if not is_clock else fire_at.strftime("%H:%M")
    return ActionResponse(
        Action.REQLLM,
        (
            f"提醒已设好，编号 {rid}：{when} 提醒「{text}」"
            f"{'，每天重复' if daily else ''}。请用一句口语化的话跟主人确认，"
            f"比如「好嘞，{when} 我叫你」。"
        ),
        None,
    )


list_reminders_desc = {
    "type": "function",
    "function": {
        "name": "list_reminders",
        "description": "看一眼当前设了哪些提醒。当主人问「我设了哪些提醒」时调用。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


@register_function("list_reminders", list_reminders_desc, ToolType.WAIT)
def list_reminders():
    try:
        items = _np()._load_reminders()
    except Exception as e:
        return ActionResponse(Action.REQLLM, f"读提醒失败（{e}）。", None)
    active = [it for it in items if it.get("enabled", True)]
    if not active:
        return ActionResponse(Action.REQLLM, "现在一条提醒都没设。口语化地告诉主人。", None)
    lines = [
        f"{it.get('at')} 「{it.get('text')}」{'（每天）' if it.get('daily') else ''}"
        for it in active
    ]
    return ActionResponse(
        Action.REQLLM, "当前提醒有：" + "；".join(lines) + "。请口语化地念给主人听。", None
    )


cancel_reminder_desc = {
    "type": "function",
    "function": {
        "name": "cancel_reminder",
        "description": (
            "取消一条提醒。主人说「把那个提醒取消了吧」时调用。"
            "最好先调 list_reminders 找到编号再取消。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reminder_id": {
                    "type": "string",
                    "description": "提醒编号，形如 r1。",
                }
            },
            "required": ["reminder_id"],
        },
    },
}


@register_function("cancel_reminder", cancel_reminder_desc, ToolType.WAIT)
def cancel_reminder(reminder_id: str):
    rid = (reminder_id or "").strip()
    try:
        np = _np()
        items = np._load_reminders()
    except Exception as e:
        return ActionResponse(Action.REQLLM, f"读提醒失败（{e}）。", None)
    left = [it for it in items if it.get("id") != rid]
    if len(left) == len(items):
        return ActionResponse(Action.REQLLM, f"没找到编号 {rid} 的提醒，请确认一下。", None)
    np._save_reminders(left)
    return ActionResponse(Action.REQLLM, f"提醒 {rid} 已取消。请口语化地告诉主人。", None)
