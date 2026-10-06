"""
小智全链路体检（标品组件）

一条命令看清整条链路上哪儿是好的、哪儿坏了。排障时先跑这个，别上来就猜。

用法：
  python tools/doctor.py            # 完整体检
  python tools/doctor.py --json     # 机器可读（给自动化用）

检查项：端口 / 进程 / 看门狗 / 开机自启 / 设备在线 / MCP 服务 / 磁盘 / 最近报错
"""
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server" / "xiaozhi-server"
LOG = ROOT / "server" / "_logs" / "server.out.log"
STARTUP = Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup"

PORTS = {8000: "主服务 WS", 8003: "OTA+视觉", 8004: "主动播报", 8005: "管理台"}
DEV_MAC = "84:c7:bb:58:17:44"

ok, warn, bad = "  [OK]  ", "  [WARN]", "  [BAD] "
issues = []


def say(tag: str, name: str, detail: str = "") -> None:
    print(f"{tag} {name}" + (f" — {detail}" if detail else ""))
    if tag == bad:
        issues.append(name)


def port_alive(p: int, host: str = "127.0.0.1") -> bool:
    s = socket.socket()
    s.settimeout(1.0)
    try:
        return s.connect_ex((host, p)) == 0
    finally:
        s.close()


def tail_text(path: Path, bytes_: int = 300_000) -> str:
    """大日志只读尾部，别整个读进来"""
    if not path.exists():
        return ""
    size = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(max(0, size - bytes_))
        return f.read().decode("utf-8", errors="replace")


def procs(name: str) -> list:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=15)
        txt = out.stdout.decode("gbk", errors="replace")
        return [l for l in txt.splitlines() if name.lower() in l.lower()]
    except Exception:
        return []


def section(t: str) -> None:
    print(f"\n── {t} " + "─" * (46 - len(t) * 2))


def main() -> None:
    print("=" * 60)
    print(f" 小智体检  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 60)

    # 1 端口
    section("服务端口")
    for p, desc in PORTS.items():
        say(ok if port_alive(p) else bad, f"{p} {desc}",
            "LISTENING" if port_alive(p) else "端口不通 → 服务没起来")

    # 2 进程
    section("进程")
    py = procs("python.exe") + procs("pythonw.exe")
    say(ok if py else bad, f"python 进程 {len(py)} 个",
        "（app.py + admin.py，正常是 2 个；加看门狗是 3 个）")
    wd = procs("pythonw.exe")
    say(ok if wd else warn, "看门狗 pythonw",
        "在跑，服务挂了会自动拉" if wd else "没跑 → 服务挂了不会自愈")

    # 3 开机自启
    section("开机自启")
    vbs = STARTUP / "XiaozhiSvcWatchdog.vbs"
    say(ok if vbs.exists() else bad, "启动文件夹 VBS",
        str(vbs.name) if vbs.exists() else "没装 → 重启电脑后后台不会起来")

    # 4 设备
    section("设备")
    txt = tail_text(LOG)
    ups = re.findall(r"(\d{6} \d{2}:\d{2}:\d{2}).*?设备上线: " + DEV_MAC, txt)
    downs = re.findall(r"(\d{6} \d{2}:\d{2}:\d{2}).*?设备下线: " + DEV_MAC, txt)
    last_up = ups[-1] if ups else ""
    last_down = downs[-1] if downs else ""
    if last_up and (not last_down or last_up > last_down):
        say(ok, f"设备在线（最后上线 {last_up}）", DEV_MAC)
    else:
        say(bad, "设备不在线", f"最后上线 {last_up or '从未'} / 最后下线 {last_down or '无'}"
            + " → 检查设备 Wi-Fi（ESP32-S3 只支持 2.4G）")

    # 5 MCP
    section("MCP 服务")
    cfg = SERVER / "data" / ".mcp_server_settings.json"
    if not cfg.exists():
        say(warn, "MCP 配置", "不存在（不配也能跑，只是没有 MCP 工具）")
    else:
        try:
            d = json.loads(cfg.read_text(encoding="utf-8"))
            en, dis = d.get("mcpServers", {}), d.get("_disabled", {})
            say(ok, f"配置存在：启用 {len(en)} 个 / 预置 {len(dis)} 个",
                ", ".join(en.keys()) or "（无）")
            loaded = re.findall(r"服务端MCP客户端已连接，可用工具: (\[[^\]]*\]?)", txt)
            if loaded:
                # ★ 绝不用 eval 解析日志内容（外部输入）。literal_eval 只接受字面量。
                import ast
                try:
                    names = ast.literal_eval(loaded[-1].replace("...", ""))
                    say(ok, f"最近一次加载成功：{len(names)} 个工具", ", ".join(names[:12]))
                except Exception:
                    say(ok, "最近一次加载成功", loaded[-1][:80])
            else:
                say(warn, "日志里没看到 MCP 加载成功记录",
                    "可能是本次启动前还没配，或初始化超时（服务端只给 10 秒）")
            fails = re.findall(r"Failed to initialize MCP server (\w+): (\w+)", txt)
            if fails:
                say(warn, f"有 {len(fails)} 次 MCP 初始化失败", str(fails[-3:]))
        except Exception as e:
            say(bad, "MCP 配置解析失败", f"{type(e).__name__}: {e}")

    # 6 磁盘
    section("磁盘")
    try:
        import shutil
        for d in [ROOT.drive + "\\", "D:\\"]:
            try:
                t, u, f = shutil.disk_usage(d)
                g = f / 1024 ** 3
                say(ok if g > 10 else warn, f"{d} 剩余 {g:.1f} GB")
            except Exception:
                pass
    except Exception:
        pass

    # 7 最近报错
    section("最近报错（日志尾部）")
    errs = [l for l in txt.splitlines() if "-ERROR-" in l][-5:]
    if errs:
        for e in errs:
            print(f"  {e[-150:]}")
        say(warn, f"尾部有 {len(errs)} 条 ERROR（只显示最后 5 条）")
    else:
        say(ok, "日志尾部无 ERROR")

    # 结论
    print("\n" + "=" * 60)
    if issues:
        print(f" 结论：有 {len(issues)} 项需要处理")
        for i in issues:
            print(f"   ✗ {i}")
    else:
        print(" 结论：全链路正常 ✅")
    print("=" * 60)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
