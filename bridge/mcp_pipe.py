#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcp_pipe.py —— wss <-> stdio 哑管道

把小智云端 MCP 接入点（wss://api.xiaozhi.me/mcp/?token=xxx）接到本机 MCP Server 的 stdin/stdout。
本进程只做搬运，不解析 JSON-RPC 内容。

用法：
    python mcp_pipe.py server_agent.py
"""
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

ENDPOINT = os.getenv("MCP_ENDPOINT", "").strip()
RETRY = float(os.getenv("MCP_RETRY_SECONDS", "3"))

try:  # websockets >= 13
    import websockets.asyncio.client as _ws

    connect = _ws.connect
except ImportError:  # 老版本
    import websockets

    connect = websockets.connect


async def run_session(target_cmd):
    proc = await asyncio.create_subprocess_exec(
        *target_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE),
    )

    async def ws_to_proc(ws):
        async for msg in ws:
            data = msg if isinstance(msg, (bytes, bytearray)) else msg.encode("utf-8")
            if not data.endswith(b"\n"):
                data += b"\n"
            proc.stdin.write(data)
            await proc.stdin.drain()

    async def proc_to_ws(ws):
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                await ws.send(text)

    async def log_stderr():
        # 子进程中文 stderr 可能是 cp936，必须用 replace，否则解码异常会断链
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            sys.stderr.write("[agent] " + line.decode("utf-8", errors="replace"))
            sys.stderr.flush()

    asyncio.create_task(log_stderr())
    async with connect(ENDPOINT, ping_interval=20, ping_timeout=20, max_size=None) as ws:
        print(f"[pipe] connected -> {ENDPOINT.split('?')[0]}")
        await asyncio.gather(ws_to_proc(ws), proc_to_ws(ws))


async def main():
    if not ENDPOINT:
        sys.exit("MCP_ENDPOINT 未设置：请复制 .env.example 为 .env 并填入接入点地址")

    target = sys.argv[1:] or [str(BASE / "server_agent.py")]
    if len(target) == 1 and target[0].endswith(".py"):
        target = [sys.executable, target[0]]

    while True:
        try:
            await run_session(target)
        except Exception as e:  # 断线自动重连
            print(f"[pipe] disconnected: {e!r} —— {RETRY}s 后重连")
            await asyncio.sleep(RETRY)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[pipe] bye")
