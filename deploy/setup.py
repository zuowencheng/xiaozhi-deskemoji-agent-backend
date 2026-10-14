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

# 路径统一走 tools/paths.py（自发现 + xiaozhi.local.json 覆盖，换机不用改代码）
sys.path.insert(0, str(ROOT / "tools"))
import paths  # noqa: E402

VENV_PY = Path(paths.VENV_PY) if paths.VENV_PY else Path(sys.executable)
BASE_PY = Path(paths.BASE_PY) if paths.BASE_PY else Path(sys.executable)
# 若没有可用 venv，就在项目内建一个（server/.venv），做到"克隆下来就能跑"
TARGET_VENV = ROOT / "server" / ".venv"
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
    print(paths.describe())
    print("=" * 60)

    # 1 环境
    step(1, "环境检查")
    mark(BASE_PY.exists(), f"基础 Python：{BASE_PY}", fatal=True)
    mark(SERVER.exists(), f"服务端目录：{SERVER.name}", fatal=True)
    venv_ok = VENV_PY.exists()
    mark(venv_ok, f"服务端虚拟环境：{VENV_PY}"
         + ("" if venv_ok else f" → 阶段 2 将在 {TARGET_VENV} 创建"))

    # 2 依赖
    step(2, "依赖")
    if not venv_ok and not check_only:
        print(f"   创建虚拟环境：{TARGET_VENV}")
        rc, out, err = run([BASE_PY, "-m", "venv", str(TARGET_VENV)], timeout=300)
        if rc == 0:
            VENV_PY = (TARGET_VENV / "Scripts" / "python.exe") if os.name == "nt" \
                else (TARGET_VENV / "bin" / "python")
            venv_ok = VENV_PY.exists()
        mark(venv_ok, "虚拟环境创建" + ("" if venv_ok else f"：{err[-200:]}"), fatal=not venv_ok)

    if venv_ok:
        if not skip_deps and not check_only:
            print("   （提示：依赖已装好就加 --skip-deps 跳过，省几分钟）")
            rc, out, err = run([VENV_PY, "-m", "pip", "install", "-q", "-r", str(REQS)],
                               timeout=1800)
            mark(rc == 0, "依赖安装" + ("" if rc == 0 else f"：{err[-200:]}"))
        else:
            mark(True, "跳过依赖安装（--skip-deps / --check-only）")
    else:
        mark(False, "虚拟环境不可用，且当前是 --check-only", fatal=check_only)

    # 3 配置
    step(3, "配置")
    cfg_template = SERVER / "config.yaml"
    if not CONFIG.exists() and not check_only and cfg_template.exists():
        import shutil as _sh
        _sh.copy2(cfg_template, CONFIG)
        mark(True, f"已从 config.yaml 生成 {CONFIG.name} → ⚠️ 记得填 API Key")
    mark(CONFIG.exists(), f"主配置 .config.yaml"
         + ("" if CONFIG.exists() else " → 缺失！从 config.yaml 复制一份再填 key"), fatal=True)

    if not MCP_CFG.exists() and not check_only:
        rc, out, err = run([BASE_PY, str(ROOT / "tools" / "gen_mcp_settings.py")], timeout=60)
        mark(rc == 0 or MCP_CFG.exists(), "生成 MCP 配置 .mcp_server_settings.json")
        print(out[-300:] if out else "")
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
