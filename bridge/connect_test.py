#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
connect_test.py —— 只验证 MCP 接入点能不能连通（token 是否有效），不启动完整桥

连接上以后云端会主动发 initialize 之类的 JSON-RPC 请求，收到就说明链路通。
用法：python connect_test.py
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

try:
    import websockets.asyncio.client as _ws

    connect = _ws.connect
except ImportError:
    import websockets

    connect = websockets.connect

EP = os.getenv("MCP_ENDPOINT", "").strip()
if not EP:
    sys.exit("MCP_ENDPOINT 未设置，先配 .env")


async def main():
    print(f"connecting -> {EP.split('?')[0]} ...")
    try:
        async with connect(EP, open_timeout=15) as ws:
            print("[OK] WebSocket 已建立（token 有效）")
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=20)
                print(f"[OK] 收到云端消息：{msg[:400]}")
            except asyncio.TimeoutError:
                print("[WARN] 20 秒内没收到消息——通常是正常的（云端等你说话才会推），token 本身没问题")
    except Exception as e:
        print(f"[FAIL] 连接失败：{e!r}")
        print("       常见原因：token 过期/复制不全、网络不通、接入点被重置")


if __name__ == "__main__":
    asyncio.run(main())
