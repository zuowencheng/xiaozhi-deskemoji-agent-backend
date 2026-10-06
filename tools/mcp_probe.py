"""
MCP Server 自检工具（stdio 通道）

用途：
  1) 装机自检：验证 data/.mcp_server_settings.json 里配的 MCP 服务能不能起来、有哪些工具
  2) 预热：npx 类服务首次启动要下载几十秒，而服务端初始化超时只有 10 秒 →
     **装完必须先跑一次本脚本预热**，否则服务端连不上
  3) 排障：服务起来了但小智说没有某个工具时，用这个看工具列表

用法：
  python tools/mcp_probe.py                 # 探测已启用（mcpServers）
  python tools/mcp_probe.py --all           # 连预置未启用的一起探测
  python tools/mcp_probe.py --name memory   # 只探测指定服务
  python tools/mcp_probe.py --timeout 120   # 自定义超时（默认 90 秒，预热用）

注意：必须用服务端 venv 的 python 跑（要有 mcp 包）：
  C:\\Users\\zuowe\\.workbuddy\\binaries\\python\\envs\\xiaozhi-server\\Scripts\\python.exe
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "server" / "xiaozhi-server" / "data" / ".mcp_server_settings.json"


def load(kind: str) -> dict:
    if not CFG.exists():
        print(f"[!] 配置文件不存在: {CFG}")
        return {}
    data = json.loads(CFG.read_text(encoding="utf-8"))
    return data.get(kind, {})


def _fmt(e, depth=0):
    """递归展开 ExceptionGroup —— 真因往往藏在第 3 层"""
    pad = "            " + "  " * depth
    subs = getattr(e, "exceptions", None)
    if subs:
        out = [f"{pad}{type(e).__name__}:"]
        for s in subs:
            out.append(_fmt(s, depth + 1) if getattr(s, "exceptions", None)
                       else f"{pad}  └─ {type(s).__name__}: {s}")
        return "\n".join(out)
    return f"{pad}{type(e).__name__}: {e}"


async def probe(name: str, cfg: dict, timeout: float) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    if not cfg.get("command"):
        if cfg.get("url"):
            print(f"  [skip] {name}: url 模式（sse/http）不支持本工具自检，请在服务端日志看结果")
        else:
            print(f"  [skip] {name}: 既无 command 也无 url")
        return

    cmd = shutil.which("npx") if cfg["command"] == "npx" else cfg["command"]
    if not cmd:
        print(f"  [FAIL] {name}: 找不到命令 {cfg['command']}")
        return

    params = StdioServerParameters(
        command=cmd,
        args=cfg.get("args", []),
        env={**os.environ, **cfg.get("env", {})},
    )
    try:
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await asyncio.wait_for(session.initialize(), timeout=timeout)
                tools = await asyncio.wait_for(session.list_tools(), timeout=30)
                names = [t.name for t in tools.tools]
                print(f"  [OK] {name}: {len(names)} 个工具")
                for n in names:
                    print(f"       - {n}")
    except asyncio.TimeoutError:
        print(f"  [TIMEOUT] {name}: {timeout}s 内没起来")
        print(f"            → 多半是首次下载。再跑一次（已缓存）即可，或加大 --timeout")
    except Exception as e:
        # ExceptionGroup / TaskGroup 会把真因藏在子异常里，必须递归展开
        print(f"  [FAIL] {name}:")
        print(_fmt(e))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="连预置未启用的一起探测")
    ap.add_argument("--name", default="", help="只探测指定服务名")
    ap.add_argument("--timeout", type=float, default=90.0)
    a = ap.parse_args()

    enabled = load("mcpServers")
    targets = dict(enabled)
    if a.all:
        targets.update(load("_disabled"))
    if a.name:
        targets = {k: v for k, v in targets.items() if k == a.name}
        if not targets:
            print(f"[!] 没找到名为 {a.name} 的服务（已启用+预置都没匹配）")
            return

    print(f"配置文件: {CFG}")
    print(f"已启用 {len(enabled)} 个 / 本次探测 {len(targets)} 个，超时 {a.timeout}s\n")
    for name, cfg in targets.items():
        tag = "" if name in enabled else " [预置未启用]"
        print(f"→ {name}{tag}")
        await probe(name, cfg, a.timeout)
    print("\n完成。")


if __name__ == "__main__":
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
