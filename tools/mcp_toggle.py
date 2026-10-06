"""
MCP 服务开关

配置里没有 disabled 字段（上游只有 mcpServers 会被加载），
所以"预置未启用"的服务放在 _disabled 键里，本脚本负责在两个键之间搬。

用法：
  python tools/mcp_toggle.py list
  python tools/mcp_toggle.py enable playwright      # 启用后记得重启服务端
  python tools/mcp_toggle.py disable xiaozhi-local
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = ROOT / "server" / "xiaozhi-server" / "data" / ".mcp_server_settings.json"


def load() -> dict:
    if not CFG.exists():
        print(f"[!] 配置文件不存在：{CFG}")
        sys.exit(1)
    return json.loads(CFG.read_text(encoding="utf-8"))


def save(data: dict) -> None:
    CFG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_list(d: dict) -> None:
    en = d.get("mcpServers", {})
    dis = d.get("_disabled", {})
    print(f"已启用 {len(en)} 个：")
    for n, c in en.items():
        cmd = c.get("command", c.get("url", "?"))
        print(f"  ● {n:<18} {cmd}")
    print(f"\n预置未启用 {len(dis)} 个：")
    for n, c in dis.items():
        cmd = c.get("command", c.get("url", "?"))
        note = (c.get("des", "") or "")[:60]
        print(f"  ○ {n:<18} {cmd}")
        if note:
            print(f"      └─ {note}")


def cmd_enable(d: dict, name: str) -> None:
    dis = d.setdefault("_disabled", {})
    if name in d.get("mcpServers", {}):
        print(f"[=] {name} 已经是启用状态，无需操作")
        return
    if name not in dis:
        print(f"[!] 找不到 {name}（已启用和预置里都没有）")
        return
    cfg = dis.pop(name)
    d.setdefault("mcpServers", {})[name] = cfg
    save(d)
    print(f"[✓] 已启用 {name}")
    print("    ★ 必须重启服务端才生效（MCP 配置只在连接建立时读一次）")
    print("    ★ 重启前建议先跑 python tools/mcp_probe.py 确认它能起来")


def cmd_disable(d: dict, name: str) -> None:
    en = d.get("mcpServers", {})
    if name not in en:
        print(f"[!] {name} 不在已启用列表里")
        return
    cfg = en.pop(name)
    d.setdefault("_disabled", {})[name] = cfg
    save(d)
    print(f"[✓] 已停用 {name}（配置保留在 _disabled，可随时 enable 回来）")
    print("    ★ 必须重启服务端才生效")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    d = load()
    a = sys.argv[1]
    if a == "list":
        cmd_list(d)
    elif a in ("enable", "disable") and len(sys.argv) >= 3:
        (cmd_enable if a == "enable" else cmd_disable)(d, sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
