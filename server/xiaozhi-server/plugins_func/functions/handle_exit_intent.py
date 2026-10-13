from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

handle_exit_intent_function_desc = {
    "type": "function",
    "function": {
        "name": "handle_exit_intent",
        "description": "当用户想结束对话或需要退出系统时调用",
        "parameters": {
            "type": "object",
            "properties": {
                "say_goodbye": {
                    "type": "string",
                    "description": "和用户友好结束对话的告别语",
                },
                "silent": {
                    "type": "boolean",
                    "description": (
                        "true=静默退下：什么都不说，直接回待机。"
                        "当主人说「退下吧」「你退下」「闭嘴」「安静」「别说了」"
                        "这类不想要回应的指令时使用；正常告别（再见、晚安等）不要传。"
                    ),
                },
            },
            "required": [],
        },
    },
}


def _send_standby(conn: "ConnectionHandler"):
    """让设备直接回待机（关麦+待命，只留唤醒词监听）。

    固件 application.cc 的 system/standby 分支已实现（2026-10-03），
    比 close_after_chat 断连重连干净——不断线、不显示"无法连接服务"。
    """
    import asyncio
    import json
    try:
        loop = getattr(conn, "loop", None)
        ws = getattr(conn, "websocket", None)
        if loop is not None and ws is not None and loop.is_running():
            payload = json.dumps({"type": "system", "command": "standby"})

            async def _send():
                await ws.send(payload)

            asyncio.run_coroutine_threadsafe(_send(), loop)
    except Exception as e:
        logger.bind(tag=TAG).warning(f"发送 standby 失败（设备自己也会超时回待机）: {e}")


@register_function(
    "handle_exit_intent", handle_exit_intent_function_desc, ToolType.SYSTEM_CTL
)
def handle_exit_intent(conn: "ConnectionHandler", say_goodbye: str | None = None,
                       silent: bool = False):
    # 处理退出意图
    try:
        # ★ 2026-10-13 静默退下（主人原话：「通过语音让它退下的时候，
        #   可以没有任何反馈，直接处于待命状态」）：不说告别语、不断连，
        #   发 standby 让设备关麦回待命，只剩唤醒词监听。
        if silent:
            _send_standby(conn)
            logger.bind(tag=TAG).info("静默退下：无语音反馈，设备回待机")
            return ActionResponse(
                action=Action.NONE, result="已静默退下", response=""
            )

        if say_goodbye is None:
            say_goodbye = "再见，祝您生活愉快！"
        if not conn.close_after_chat:
            conn.close_after_chat = True
        logger.bind(tag=TAG).info(f"退出意图已处理:{say_goodbye}")
        return ActionResponse(
            action=Action.RESPONSE, result="退出意图已处理", response=say_goodbye
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"处理退出意图错误: {e}")
        return ActionResponse(
            action=Action.NONE, result="退出意图处理失败", response=""
        )
