#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest_stdio.py —— 不连云端也能验证本机 MCP Server 是否健康

判据（两条都必须满足，缺一个真机就会出问题）：
  1. 工具齐全
  2. 每个工具的 description 非空  ← 写成 f-string 的 docstring 会让 description 变空串，
                                     模型就会乱选工具，这是最隐蔽的坑
"""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE = Path(__file__).resolve().parent


async def main():
    params = StdioServerParameters(
        command=sys.executable,
        args=[str(BASE / "server_agent.py")],
        cwd=str(BASE),
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            resp = await session.list_tools()
            tools = resp.tools
            print(f"\n工具数量：{len(tools)}\n" + "-" * 60)
            bad = 0
            for t in tools:
                desc = (t.description or "").strip()
                flag = "OK   " if desc else "EMPTY!"
                if not desc:
                    bad += 1
                print(f"{flag} {t.name:<20} {desc[:46]}")
            print("-" * 60)
            # 顺手真跑一个只读工具，确认不是纸面健康
            res = await session.call_tool("get_pc_status", {})
            print(f"\n实测 get_pc_status -> {res.content[0].text}\n")
            print("✅ 自检通过" if bad == 0 else f"❌ 有 {bad} 个工具 description 为空，先修")
            return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
