# -*- coding: utf-8 -*-
"""
pc_mcp · 本机 PC 控制 MCP Server（能力编号 9~22）
====================================================

为什么单独一个 Server，不并进 local_mcp
----------------------------------------
1. **分组挂载**：docs/63 纪律 3 —— 一次挂太多工具会降低模型选工具准确率。
   分两个 Server，可以用 `tools/mcp_toggle.py` 单独关掉其中一组。
2. **依赖隔离**：本组依赖 pywin32（桌面控制），local_mcp 不依赖。
   将来换开源 Windows Control MCP 替换时，只需要换这一个文件。

设计原则
--------
- **不重写实现**：全部复用 `bridge/server_agent.py`（M1/M4）与
  `core/pc_agent.py`（M2/M3）里已经跑通的函数，本文件只做 MCP 包装。
  好处是行为零漂移，坏处是强依赖那两个模块 —— 已通过 `--selftest` 验证可调用。
- **stdout 就是 JSON-RPC 协议流**：任何 print 都会让握手失败。
  日志一律写 `runtime/logs/pc_mcp.log`。
- **每个工具都包 try/except**：单个工具炸了不能拖垮整个 Server。

安全边界
--------
- `open_app` 走白名单（server_agent 内实现）
- `organize_dir` **默认 apply=False（只出计划不动文件）**，必须显式传 True 才真搬
- `window_action` 的 close 会真的关窗口，描述里已写明

用法
----
    python bridge/tools/pc_mcp.py --selftest   # 本地自检，不走 stdio
    python bridge/tools/pc_mcp.py              # stdio 模式（由服务端拉起）
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径注入：让本文件能 import 到服务端与 bridge 下的现成实现
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve()
BRIDGE_DIR = _HERE.parents[1]                      # <project>/bridge
PROJECT_ROOT = _HERE.parents[2]                    # <project>
SERVER_DIR = PROJECT_ROOT / "server" / "xiaozhi-server"

for _p in (str(SERVER_DIR), str(BRIDGE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LOG_DIR = PROJECT_ROOT / "runtime" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "pc_mcp.log"


def _log(msg: str) -> None:
    """日志写文件 —— stdio 模式下 stdout 是协议流，绝对不能 print。"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 复用现成实现（延迟 import，失败时优雅降级而不是整个 Server 起不来）
# ---------------------------------------------------------------------------
_sa = None      # bridge/server_agent.py  -> M1 系统状态 / M4 任务派发
_pa = None      # core/pc_agent.py        -> M2 桌面 / M3 文件
_import_err: list[str] = []


def _init_impls() -> None:
    global _sa, _pa
    try:
        import server_agent as sa
        _sa = sa
        _log("server_agent 加载成功")
    except Exception as e:
        _import_err.append(f"server_agent: {type(e).__name__}: {e}")
        _log(f"[ERR] server_agent 加载失败 {type(e).__name__}: {e}")
    try:
        from core import pc_agent as pa
        _pa = pa
        _log("pc_agent 加载成功")
    except Exception as e:
        _import_err.append(f"pc_agent: {type(e).__name__}: {e}")
        _log(f"[ERR] pc_agent 加载失败 {type(e).__name__}: {e}\n{traceback.format_exc()}")


_init_impls()


def _need(mod, name: str):
    """取实现模块，缺失时返回 None（由调用方转成友好文案）。"""
    if mod is None:
        _log(f"[MISS] {name} 的底层模块不可用")
        return None
    return mod


from mcp.server.fastmcp import FastMCP

mcp = FastMCP("deskemoji-pc")


# ---------------------------------------------------------------------------
# M1 · 系统状态（9 / 10）
# ---------------------------------------------------------------------------
@mcp.tool()
def get_pc_status() -> str:
    """查看这台电脑的运行状态：CPU 占用、内存占用、磁盘剩余。主人问"电脑卡不卡""还剩多少空间"时用。"""
    try:
        if _need(_sa, "get_pc_status") is None:
            return "[失败] 系统状态模块没加载起来，看下日志。"
        return _sa.get_pc_status()
    except Exception as e:
        _log(f"[ERR] get_pc_status {type(e).__name__}: {e}")
        return f"[失败] 查系统状态出错：{type(e).__name__}"


@mcp.tool()
def list_running_apps(limit: int = 12) -> str:
    """列出这台电脑上正在运行的程序。主人问"现在开着什么""某某程序在不在跑"时用。"""
    try:
        if _need(_sa, "list_running_apps") is None:
            return "[失败] 进程模块没加载起来，看下日志。"
        return _sa.list_running_apps(limit=int(limit or 12))
    except Exception as e:
        _log(f"[ERR] list_running_apps {type(e).__name__}: {e}")
        return f"[失败] 列进程出错：{type(e).__name__}"


# ---------------------------------------------------------------------------
# M2 · 桌面控制（11 ~ 16）
# ---------------------------------------------------------------------------
@mcp.tool()
def open_app(name: str) -> str:
    """打开电脑上的一个程序（有白名单，只放行已知程序）。主人说"打开计算器""开个记事本"时用。"""
    try:
        if _need(_sa, "open_app") is None:
            return "[失败] 打开程序的模块没加载起来，看下日志。"
        return _sa.open_app(name or "")
    except Exception as e:
        _log(f"[ERR] open_app {type(e).__name__}: {e}")
        return f"[失败] 打开程序出错：{type(e).__name__}"


@mcp.tool()
def list_windows(limit: int = 40) -> str:
    """列出当前打开的窗口标题。主人问"我现在开着哪些窗口""屏幕上有什么"时用。"""
    try:
        if _need(_pa, "list_windows") is None:
            return "[失败] 桌面模块没加载起来，看下日志。"
        return _pa.tool_list_windows(limit=int(limit or 40))
    except Exception as e:
        _log(f"[ERR] list_windows {type(e).__name__}: {e}")
        return f"[失败] 列窗口出错：{type(e).__name__}"


@mcp.tool()
def window_action(title: str = "", action: str = "focus", hwnd: int = 0) -> str:
    """对某个窗口做动作：focus 置前、minimize 最小化、maximize 最大化、restore 还原、close 关闭。
    close 会真的关掉窗口，主人没明说要关就别用。用窗口标题的一部分就能匹配，不用写全名。"""
    try:
        if _need(_pa, "window_action") is None:
            return "[失败] 桌面模块没加载起来，看下日志。"
        return _pa.tool_window_action(title or "", action or "focus", int(hwnd or 0))
    except Exception as e:
        _log(f"[ERR] window_action {type(e).__name__}: {e}")
        return f"[失败] 窗口操作出错：{type(e).__name__}"


@mcp.tool()
def send_keys(keys: str = "", title: str = "", delay: float = 0.05) -> str:
    """给电脑发按键或组合键，比如 ctrl+c 复制、alt+tab 切换、win+d 回桌面、enter 回车。
    也可以直接打字。可以先用 title 把某个窗口置前再发。"""
    try:
        if _need(_pa, "send_keys") is None:
            return "[失败] 桌面模块没加载起来，看下日志。"
        return _pa.tool_send_keys(keys or "", title or "", float(delay or 0.05))
    except Exception as e:
        _log(f"[ERR] send_keys {type(e).__name__}: {e}")
        return f"[失败] 发按键出错：{type(e).__name__}"


@mcp.tool()
def screenshot(save_to: str = "", title: str = "") -> str:
    """截一张屏幕图片存成 PNG。save_to 留空就存桌面；给了 title 就只截那个窗口。"""
    try:
        if _need(_pa, "screenshot") is None:
            return "[失败] 桌面模块没加载起来，看下日志。"
        return _pa.tool_screenshot(save_to or "", title or "")
    except Exception as e:
        _log(f"[ERR] screenshot {type(e).__name__}: {e}")
        return f"[失败] 截屏出错：{type(e).__name__}"


@mcp.tool()
def clipboard(mode: str = "get", text: str = "") -> str:
    """读(get)或写(set)剪贴板。主人说"复制下来""帮我粘上去"时用。"""
    try:
        if _need(_pa, "clipboard") is None:
            return "[失败] 桌面模块没加载起来，看下日志。"
        return _pa.tool_clipboard(mode or "get", text or "")
    except Exception as e:
        _log(f"[ERR] clipboard {type(e).__name__}: {e}")
        return f"[失败] 剪贴板操作出错：{type(e).__name__}"


# ---------------------------------------------------------------------------
# M3 · 文件（17 / 18）
# ---------------------------------------------------------------------------
@mcp.tool()
def open_path(target: str) -> str:
    """用资源管理器打开一个文件夹、文件、程序或网址，等价于双击。"""
    try:
        if _need(_pa, "open_path") is None:
            return "[失败] 打开模块没加载起来，看下日志。"
        return _pa.tool_open_path(target or "")
    except Exception as e:
        _log(f"[ERR] open_path {type(e).__name__}: {e}")
        return f"[失败] 打开失败：{type(e).__name__}"


@mcp.tool()
def organize_dir(path: str = "", mode: str = "by_type", apply: bool = False,
                 max_files: int = 50) -> str:
    """整理一个文件夹：把文件按类型(by_type)或扩展名(by_ext)归到子文件夹里。
    安全设计：默认 apply=False 只出计划、不动任何文件；主人确认后传 apply=True 才真的搬。
    只搬文件不动子目录，绝不删除、绝不覆盖，重名自动加序号。"""
    try:
        if _need(_pa, "organize_dir") is None:
            return "[失败] 整理模块没加载起来，看下日志。"
        return _pa.tool_organize_dir(path or "", mode or "by_type",
                                     bool(apply), int(max_files or 50))
    except Exception as e:
        _log(f"[ERR] organize_dir {type(e).__name__}: {e}")
        return f"[失败] 整理目录出错：{type(e).__name__}"


# ---------------------------------------------------------------------------
# M4 · 任务派发（19 ~ 22）
# ---------------------------------------------------------------------------
@mcp.tool()
def dispatch_agent(task: str) -> str:
    """派一件复杂的活给电脑助理去做，比如"查明天北京天气写个 txt 放桌面"。
    派出去后会有人在后台干，干完会主动告诉你，不用一直问。一次只派一件。"""
    try:
        if _need(_sa, "dispatch_agent") is None:
            return "[失败] 派单模块没加载起来，看下日志。"
        return _sa.dispatch_agent(task or "")
    except Exception as e:
        _log(f"[ERR] dispatch_agent {type(e).__name__}: {e}")
        return f"[失败] 派单出错：{type(e).__name__}"


@mcp.tool()
def query_task(task_id: str) -> str:
    """按任务编号查一件派出去的活的进展和结果。"""
    try:
        if _need(_sa, "query_task") is None:
            return "[失败] 任务模块没加载起来，看下日志。"
        return _sa.query_task(task_id or "")
    except Exception as e:
        _log(f"[ERR] query_task {type(e).__name__}: {e}")
        return f"[失败] 查任务出错：{type(e).__name__}"


@mcp.tool()
def list_recent_tasks(limit: int = 5) -> str:
    """列出最近派过的几件活和它们的状态。主人问"我之前让你干的那事怎么样了"时用。"""
    try:
        if _need(_sa, "list_recent_tasks") is None:
            return "[失败] 任务模块没加载起来，看下日志。"
        return _sa.list_recent_tasks(limit=int(limit or 5))
    except Exception as e:
        _log(f"[ERR] list_recent_tasks {type(e).__name__}: {e}")
        return f"[失败] 列任务出错：{type(e).__name__}"


@mcp.tool()
def get_pending_results() -> str:
    """取回刚刚干完、还没向主人汇报过的任务结果。念完一次就标记已汇报，不会重复念。"""
    try:
        if _need(_sa, "get_pending_results") is None:
            return "[失败] 任务模块没加载起来，看下日志。"
        return _sa.get_pending_results()
    except Exception as e:
        _log(f"[ERR] get_pending_results {type(e).__name__}: {e}")
        return f"[失败] 取结果出错：{type(e).__name__}"


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
async def _selftest() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    import os

    print("=" * 64)
    print("pc_mcp 自检")
    print("=" * 64)
    if _import_err:
        print("\n[!] 底层模块加载有告警：")
        for e in _import_err:
            print("    -", e)
    else:
        print("\n[OK] 两个底层模块都加载成功（server_agent / pc_agent）")

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(_HERE)],
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = [t.name for t in tools.tools]
            print(f"\n[工具总数] {len(names)}")
            for t in tools.tools:
                desc = (t.description or "").strip()
                flag = "OK " if desc else "!! 描述为空"
                print(f"  {flag} {t.name:<20} {desc[:44]}")
            empty = [t.name for t in tools.tools if not (t.description or "").strip()]
            if empty:
                print(f"\n[FAIL] 这些工具 description 为空：{empty}")
                return 1

            # 真跑几个只读工具，确认调用链通的（不能只看 registered）
            print("\n--- 实调用（只读）---")
            for call in (
                ("get_pc_status", {}),
                ("list_running_apps", {"limit": 5}),
                ("list_windows", {"limit": 5}),
                ("list_recent_tasks", {"limit": 3}),
                ("get_pending_results", {}),
                ("organize_dir", {"path": str(Path.home() / "Desktop"), "apply": False}),
            ):
                try:
                    res = await s.call_tool(call[0], call[1])
                    txt = ""
                    for c in res.content:
                        txt += getattr(c, "text", "") or ""
                    print(f"  [OK] {call[0]:<20} -> {txt[:90]}")
                except Exception as e:
                    print(f"  [!!] {call[0]:<20} -> {type(e).__name__}: {e}")

            print("\n全部检查完成。")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        import asyncio

        sys.exit(asyncio.run(_selftest()))
    mcp.run()
