# -*- coding: utf-8 -*-
"""诊断：合并后配置的真实值 + 空闲断开参数"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRV = os.path.join(os.path.dirname(HERE), "server", "xiaozhi-server")
sys.path.insert(0, SRV)
os.chdir(SRV)

from config.config_loader import load_config


async def main():
    c = await load_config()
    keys = [
        "close_connection_no_voice_time",
        "enable_websocket_ping",
        "enable_greeting",
        "enable_wakeup_words_response_cache",
        "delete_audio",
        "tts_timeout",
        "tool_call_timeout",
    ]
    print("=== merged config (data/.config.yaml 覆盖 config.yaml 后) ===")
    for k in keys:
        print("  %-38s = %r" % (k, c.get(k)))
    print()
    nv = c.get("close_connection_no_voice_time")
    print("=== 推导 ===")
    try:
        print("  无语音断开阈值      = %s 秒" % nv)
        print("  第二道超时(阈值+60) = %s 秒" % (int(nv) + 60))
    except Exception as e:
        print("  无法推导:", e)
    print()
    print("=== end_prompt ===")
    print("  ", c.get("end_prompt"))


asyncio.run(main())
