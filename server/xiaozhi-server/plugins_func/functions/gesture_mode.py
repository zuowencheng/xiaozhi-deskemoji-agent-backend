# -*- coding: utf-8 -*-
"""gesture_mode.py —— 语音开关「手势识别模式」（2026-10-13）

主人原话：
  「处于聆听模式下：对它说'开启手势识别'。开启后，它只支持手势识别，
    不再自动、随机地做动作。开启手势识别后，它会说'已开启手势识别'；
    关闭手势识别之后，它就会说'已关闭手势识别'并让它处于待命的状态下，
    仍然只支持手势识别；在关闭手势识别的情况下，待命的时候可以自行做动作。」

两个状态（开机默认 = 关闭）：
  关闭：待命只认掌心前推（开/关聆听），其余手势忽略；待命时随机卖萌动画照常。
  开启：待命/聆听两态都全响应手势（左右上下挥手都带头+表情），随机卖萌全停；
        LLM 语音回复带的表情动画保留（那是对话反馈，不是随机动作）。

设备侧：system/gesture_mode 命令 → 固件 GestureSensor::SetGestureModeEnabled
        （内部级联 EmojiController::SetRandomAnimationEnabled，一行切换）。

注意「开启后语音退出不清模式」：手势模式是独立标志，对话结束回待命、
  甚至说"退下"进待机都不动它——只有再调本工具（false）才关。
  设备重启后回到默认（关闭），与"默认关闭手势识别"的产品决策一致。
"""
from __future__ import annotations

import asyncio
import json

from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse, ToolType, register_function

TAG = __name__
logger = setup_logging()

set_gesture_mode_desc = {
    "type": "function",
    "function": {
        "name": "set_gesture_mode",
        "description": (
            "开启或关闭「手势识别模式」。"
            "当主人说「开启手势识别 / 打开手势识别 / 手势模式」时 enabled=true；"
            "说「关闭手势识别 / 关掉手势 / 退出手势模式」时 enabled=false。"
            "开启后：左右上下等所有手势都会被响应（带头部和表情），"
            "且待机时不再自动做随机动作；关闭后：只有掌心前推能开启/关闭聆听，"
            "待机时恢复随机动作。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "true=开启手势识别，false=关闭手势识别",
                }
            },
            "required": ["enabled"],
        },
    },
}


@register_function("set_gesture_mode", set_gesture_mode_desc, ToolType.SYSTEM_CTL)
def set_gesture_mode(conn, enabled: bool = False):
    """切换手势识别模式：通知设备 + 直接回复固定话术（不再过 LLM）。"""
    enabled = bool(enabled)
    try:
        # ① 通知设备（固件 application.cc 的 system/gesture_mode 分支）。
        #    工具函数是同步的，ws.send 是协程 → 借 conn.loop 丢过去。
        loop = getattr(conn, "loop", None)
        ws = getattr(conn, "websocket", None)
        if loop is not None and ws is not None and loop.is_running():
            payload = json.dumps({
                "type": "system",
                "command": "gesture_mode",
                "enabled": enabled,
            })

            async def _send():
                await ws.send(payload)

            asyncio.run_coroutine_threadsafe(_send(), loop)
        else:
            logger.bind(tag=TAG).warning(
                "set_gesture_mode: conn.loop/websocket 不可用，只改话术没通知到设备")

        # ② 固定话术直接播（Action.RESPONSE）——不走 LLM 二次生成，
        #    延迟低且不会被模型改成别的句子。
        text = "已开启手势识别" if enabled else "已关闭手势识别"
        logger.bind(tag=TAG).info(f"手势识别模式 -> {'开启' if enabled else '关闭'}")
        return ActionResponse(action=Action.RESPONSE, result=text, response=text)
    except Exception as e:
        logger.bind(tag=TAG).error(f"set_gesture_mode 失败: {e}")
        return ActionResponse(
            action=Action.RESPONSE,
            result="切换失败",
            response="手势识别切换没成功，稍后再试试",
        )
