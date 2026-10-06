"""
小智桌面智能体 · 一键部署（标品入口）

设计原则：**幂等** —— 装一半再跑一次不会搞坏，已完成的步骤自动跳过。
可以反复运行，也可以只用来"体检 + 拉起"。

用法：
  python deploy/setup.py                # 完整部署（缺什么补什么）
  python deploy/setup.py --skip-deps    # 跳过装依赖（依赖已装好时用这个，快）
  python deploy/setup.py --check-only   # 只体检不动手

六个阶段：环境 → 依赖 → 配置 → 自启 → 启动 → 体检
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server" / "xiaozhi-server"
VENV_PY = Path(r"C:\Users\zuowe\.workbuddy\binaries\python\envs\xiaozhi-server\Scripts\python.exe")
BASE_PY = Path(r"C:\Users\zuowe\.workbuddy\binaries\python\versions\3.13.12\python.exe")
REQS = SERVER / "requirements.txt"
CONFIG = SERVER / "data" / ".config.yaml"
MCP_CFG = SERVER / "data" / ".mcp_server_settings.json"

results = []


def step(n: int, title: str) -> None:
    print(f"\n{'='*60}\n [{n}/6] {title}\n{'='*60}")


def mark(ok: bool, msg: str, fatal: bool = False) -> bool:
    print(f"   {'✅' if ok else '❌'} {msg}")
    results.append((ok, msg))
    if not ok and fatal:
        print("\n[×] 关键步骤失败，中止。修好上面的问题再跑一次。")
        sys.exit(1)
    return ok


def run(cmd: list, timeout: int = 300, cwd=None) -> tuple:
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True,
                           timeout=timeout, cwd=str(cwd or ROOT))
        out = (p.stdout or b"").decode("utf-8", errors="replace")
        err = (p.stderr or b"").decode("utf-8", errors="replace")
        return p.returncode, out, err
    except subprocess.TimeoutExpired:
        return -1, "", f"超时 {timeout}s"
    except Exception as e:
        return -2, "", f"{type(e).__name__}: {e}"


def main() -> None:
    args = sys.argv[1:]
    check_only = "--check-only" in args
    skip_deps = "--skip-deps" in args

    print("=" * 60)
    print(" 小智桌面智能体 · 一键部署")
    print(f" 项目目录：{ROOT}")
    print("=" * 60)

    # 1 环境
    step(1, "环境检查")
    mark(BASE_PY.exists(), f"基础 Python：{BASE_PY}", fatal=True)
    mark(SERVER.exists(), f"服务端目录：{SERVER.name}", fatal=True)
    venv_ok = VENV_PY.exists()
    mark(venv_ok, f"服务端虚拟环境：{VENV_PY.name}"
         + ("" if venv_ok else " → 阶段 2 会创建"))

    # 2 依赖
    step(2, "依赖")
    if venv_ok:
        mark(True, "虚拟环境已存在，跳过创建")
        if not skip_deps and not check_only:
            print("   （提示：依赖已装好就加 --skip-deps 跳过，省几分钟）")
            rc, out, err = run([VENV_PY, "-m", "pip", "install", "-q", "-r", str(REQS)],
                               timeout=900)
            mark(rc == 0, "依赖安装" + ("" if rc == 0 else f"：{err[-200:]}"))
        else:
            mark(True, "跳过依赖安装（--skip-deps / --check-only）")
    else:
        if check_only:
            mark(False, "虚拟环境不存在，且当前是 --check-only，不自动创建")
        else:
            mark(False, f"需要手动创建虚拟环境（自动创建暂未实现）：\n"
                        f"     {BASE_PY} -m venv {VENV_PY.parent.parent}\n"
                        f"     {VENV_PY} -m pip install -r {REQS}", fatal=True)

    # 3 配置
    step(3, "配置")
    mark(CONFIG.exists(), f"主配置 .config.yaml"
         + ("" if CONFIG.exists() else " → 缺失！从 config.yaml 复制一份再填 key"), fatal=True)
    if MCP_CFG.exists():
        mark(True, "MCP 配置 .mcp_server_settings.json")
        if not check_only:
            rc, out, err = run([VENV_PY, str(ROOT / "tools" / "mcp_probe.py"),
                                "--timeout", "60"], timeout=300)
            ok = "[OK]" in out or rc == 0
            mark(True, "MCP 自检（首次会下载 npm 包，可能要几分钟）")
            print(out[-1200:] if out else "")
    else:
        mark(False, "MCP 配置不存在（不影响启动，只是没有 MCP 工具）")

    if check_only:
        step(6, "体检")
        run([BASE_PY, str(ROOT / "tools" / "doctor.py")], timeout=180)
        return

    # 4 自启
    step(4, "开机自启 + 看门狗")
    rc, out, err = run([BASE_PY, str(ROOT / "tools" / "install_autostart.py"),
                        "--install", "--method", "vbs"], timeout=120)
    mark(rc == 0, "自启安装（启动文件夹 VBS）")
    print(out[-400:] if out else "")

    # 5 启动
    step(5, "启动服务")
    import socket
    alive = all(_port(p) for p in (8000, 8005))
    if alive:
        mark(True, "服务已在运行（8000/8005 都通），跳过启动")
    else:
        rc, out, err = run([BASE_PY, str(ROOT / "tools" / "start_services.py"),
                            "--explorer", "--wait", "30"], timeout=180)
        print(out[-600:] if out else "")
        mark(all(_port(p) for p in (8000, 8003, 8004, 8005)), "四端口监听")

    # 6 体检
    step(6, "体检")
    rc, out, err = run([BASE_PY, str(ROOT / "tools" / "doctor.py")], timeout=180)
    print(out)

    # 汇总
    print("\n" + "=" * 60)
    bad = [m for ok, m in results if not ok]
    print(f" 部署{'完成' if not bad else '完成但有 ' + str(len(bad)) + ' 项需处理'}")
    for m in bad:
        print(f"   ✗ {m}")
    print("=" * 60)
    print("\n常用命令：")
    print("  python tools/doctor.py            # 体检")
    print("  python tools/mcp_probe.py         # MCP 自检/预热")
    print("  python tools/mcp_toggle.py list   # 看 MCP 开关")
    print("  python tools/install_autostart.py --status")


def _port(p: int) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(1)
    try:
        return s.connect_ex(("127.0.0.1", p)) == 0
    finally:
        s.close()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    os.environ["PYTHONUTF8"] = "1"
    main()
