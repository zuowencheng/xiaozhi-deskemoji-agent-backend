# -*- coding: utf-8 -*-
"""
小智 · 生成 MCP 挂载配置（按本机解析出的路径）
============================================================
`server/xiaozhi-server/data/.mcp_server_settings.json` 里存的是解释器与脚本的
绝对路径，属于「本机配置」——原来只能手工填。本脚本按 tools/paths.py 解析出的
真实路径自动生成，换台电脑跑一次即可。

用法：
    python tools/gen_mcp_settings.py            # 不存在才生成（安全）
    python tools/gen_mcp_settings.py --force    # 覆盖已有（先备份 .bak）
    python tools/gen_mcp_settings.py --print    # 只打印，不落盘
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import paths  # noqa: E402

TARGET = os.path.join(paths.SRV_DIR, "data", ".mcp_server_settings.json")

_README = [
    "服务端 MCP 配置（由 tools/gen_mcp_settings.py 自动生成，路径按本机解析）。",
    "只有 mcpServers 里的会被加载；_disabled 里的是预置未启用，用 tools/mcp_toggle.py 启用。",
    "_readme / des / link 只是说明，不会被解析。",
    "★ 改动后：① python tools/mcp_probe.py 自检 ② 重启服务端（配置只在连接建立时读一次）",
    "★ 服务端初始化每个 MCP 只给 10 秒，慢的服务会直接超时被丢弃（不致命，但工具就没了）",
]


def build(py, bridge):
    def entry(script, des, link):
        return {
            "command": py,
            "args": [os.path.join(bridge, "tools", script)],
            "des": des,
            "link": link,
        }

    return {
        "_readme": _README,
        "mcpServers": {
            "xiaozhi-local": entry(
                "local_mcp.py",
                "自研本机 MCP：文件读写/列目录/搜文件/内容搜索/抓网页/跑命令/时间。目录白名单与命令黑名单在脚本里",
                "bridge/tools/local_mcp.py（自研，离线可用，不依赖 npm）",
            ),
            "xiaozhi-pc": entry(
                "pc_mcp.py",
                "自研 PC 控制 MCP：系统状态/运行程序/打开程序/窗口操作/发按键/截屏/剪贴板/打开路径/整理目录/派任务/查任务",
                "bridge/tools/pc_mcp.py（自研，复用 server_agent + pc_agent 的现成实现）",
            ),
            "xiaozhi-life": entry(
                "life_mcp.py",
                "自研生活助理 MCP：记忆 4 个 / 待办 4 个 / 音乐 2 个 / 联网搜索",
                "bridge/tools/life_mcp.py（★ 挂载时必须同步摘掉插件版 memory_tools / todo_tools / pc_music_control，否则记忆工具同名冲突）",
            ),
        },
        "_disabled": {
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem",
                         os.path.join(os.path.expanduser("~"), "Desktop"),
                         os.path.join(os.path.expanduser("~"), "Documents")],
                "des": "官方文件读写。需先跑 tools/install_mcp_deps.cmd 装 npm 依赖",
                "link": "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
            },
            "windows-cli": {
                "command": "npx",
                "args": ["-y", "@simonb97/server-win-cli"],
                "des": "命令行执行。xiaozhi-local 的 run_shell 已覆盖此能力（带黑名单更安全）",
                "link": "https://github.com/SimonB97/win-cli-mcp-server",
            },
            "memory": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-memory"],
                "des": "★ 与自研 memory 工具重叠，两个一起开模型会分不清",
                "link": "https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
            },
            "playwright": {
                "command": "npx",
                "args": ["-y", "@executeautomation/playwright-mcp-server"],
                "des": "浏览器自动化。启用前需 tools/install_mcp_deps.cmd 且 npx playwright install",
                "link": "https://github.com/executeautomation/mcp-playwright",
            },
        },
    }


def main():
    force = "--force" in sys.argv
    only_print = "--print" in sys.argv

    py = paths.VENV_PY or paths.BASE_PY
    bridge = paths.BRIDGE_DIR
    data = build(py, bridge)

    if not py:
        print("[错误] 没解析到 Python 解释器，先跑 python tools/paths.py 看解析结果")
        sys.exit(1)
    for s in ("local_mcp.py", "pc_mcp.py", "life_mcp.py"):
        p = os.path.join(bridge, "tools", s)
        if not os.path.isfile(p):
            print("[警告] 找不到 MCP 脚本：%s" % p)

    text = json.dumps(data, ensure_ascii=False, indent=2)

    if only_print:
        print(text)
        return

    if os.path.isfile(TARGET) and not force:
        print("[跳过] 已存在（要覆盖加 --force）：%s" % TARGET)
        print("       解释器 = %s" % py)
        return

    os.makedirs(os.path.dirname(TARGET), exist_ok=True)
    if os.path.isfile(TARGET):
        shutil.copy2(TARGET, TARGET + ".bak")
    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(text)
    print("[完成] 已生成：%s" % TARGET)
    print("       解释器 = %s" % py)
    print("       脚本目录 = %s" % os.path.join(bridge, "tools"))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
