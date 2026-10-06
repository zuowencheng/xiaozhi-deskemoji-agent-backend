# -*- coding: utf-8 -*-
"""
pc_music_control · 让小智"唤起"电脑上的酷狗音乐（极简）
========================================================

主人拍板的方案（2026-10-03）：
    **"它只要能帮我唤起酷狗，剩下的我自己操作就行，没必要搞复杂。"**

所以本模块只暴露两个工具：

1. `pc_open_music_app`
   把酷狗打开/切到前台。已经在跑就还原窗口 + 置前；没跑就启动它。
   不模拟输入、不点界面元素、不搜索歌曲 —— 那些又脆又抢焦点，性价比极低。

2. `pc_music_control`
   播放/暂停、上一首、下一首、停止 —— 走系统级「媒体键」，
   对任何注册成媒体会话的播放器都有效，零依赖、不抢焦点。

设计原则：任何失败都只返回一句人话，绝不抛异常（语音侧要直接播报）。

注册方式：在 `data/.config.yaml` 的 `Intent.function_call.functions` 里加 `pc_music_control`。
"""

from core import pc_music

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()


# ---------------------------------------------------------------- 工具 1：唤起酷狗
pc_open_music_app_desc = {
    "type": "function",
    "function": {
        "name": "pc_open_music_app",
        "description": (
            "在主人的电脑上打开「酷狗音乐」这个软件，并把它切到最前面。"
            "当主人说「打开酷狗」「帮我唤起酷狗」「把酷狗开一下」"
            "「用电脑放歌，帮我打开播放器」时调用本工具。"
            "注意：本工具只负责把软件打开，不负责搜索或播放具体歌曲——"
            "打开之后由主人自己操作。所以回复时要说「已经帮你把酷狗打开了」，"
            "绝对不要说「已经帮你播上了某首歌」。"
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


@register_function("pc_open_music_app", pc_open_music_app_desc, ToolType.WAIT)
def pc_open_music_app():
    """唤起本机酷狗音乐（未运行则启动，已运行则置前）。"""
    try:
        msg = pc_music.open_player()
    except Exception as e:
        logger.bind(tag=TAG).error(f"唤起酷狗失败: {e}")
        msg = f"唤起酷狗时出了点问题（{e}），请主人手动开一下吧。"
    logger.bind(tag=TAG).info(f"pc_open_music_app -> {msg}")
    return ActionResponse(Action.REQLLM, msg + "请用口语化的一句话转述给主人。", None)


# ---------------------------------------------------------------- 工具 2：传输控制
pc_music_control_desc = {
    "type": "function",
    "function": {
        "name": "pc_music_control",
        "description": (
            "控制电脑上正在播放的音乐：播放/暂停、上一首、下一首、停止。"
            "当主人说「暂停一下」「继续放」「下一首」「换一首」「上一首」"
            "「回到上一首」「别放了/停止播放」时调用。"
            "作用对象是电脑上当前活跃的播放器（酷狗等），不会影响音箱的语音播报。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "要执行的动作，取值：play_pause（播放或暂停）、"
                        "next（下一首）、prev（上一首）、stop（停止）。"
                    ),
                    "enum": ["play_pause", "next", "prev", "stop"],
                }
            },
            "required": ["action"],
        },
    },
}


@register_function("pc_music_control", pc_music_control_desc, ToolType.WAIT)
def pc_music_control(action: str):
    """媒体键控制电脑播放器。"""
    try:
        msg = pc_music.transport(action)
    except Exception as e:
        logger.bind(tag=TAG).error(f"媒体键控制失败: {e}")
        msg = f"发送播放控制指令时出错了（{e}）。"
    logger.bind(tag=TAG).info(f"pc_music_control({action}) -> {msg}")
    return ActionResponse(Action.REQLLM, msg + "请用口语化的一句话回给主人。", None)
