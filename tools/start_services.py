# -*- coding: utf-8 -*-
"""
小智后台服务 · 脱离会话启动器
------------------------------------------------------------
目的：把 xiaozhi-server(8000/8003/8004) 与 admin(8005) 以
      DETACHED_PROCESS 方式拉起 —— 脱离父进程(与 AI 会话)的进程树，
      避免"回合结束整棵进程被回收"。

用法：
    python tools/start_services.py            # 启动
    python tools/start_services.py --status   # 只查状态
    python tools/start_services.py --stop     # 按日志中记录的 pid 停
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time

# ---- 路径 ----
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import paths  # 统一路径解析（tools/paths.py）：自发现 + xiaozhi.local.json 覆盖

ROOT = paths.ROOT
SRV_DIR = paths.SRV_DIR
ADMIN_DIR = paths.ADMIN_DIR
LOG_DIR = paths.LOG_DIR

VENV_PY = paths.VENV_PY or paths.BASE_PY
VENV_PY_ADMIN = VENV_PY
FFMPEG_BIN = paths.FFMPEG_BIN

PORTS = {
    8000: "WS 语音",
    8003: "OTA+视觉",
    8004: "主动播报",
    8005: "管理台",
}

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def port_open(port, host="127.0.0.1", timeout=1.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def status():
    print("=== 端口状态 ===")
    for p, name in PORTS.items():
        print("  %-6d %-10s %s" % (p, name, "OPEN" if port_open(p) else "closed"))
    return {p: port_open(p) for p in PORTS}


def build_env():
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if FFMPEG_BIN and os.path.isdir(FFMPEG_BIN):
        env["PATH"] = FFMPEG_BIN + ";" + env.get("PATH", "")
    env.pop("MSYSTEM", None)  # ESP-IDF 的坑，这里也顺手清掉
    return env


def _popen_detached(args, cwd, log_path, flags):
    f = open(log_path, "ab", buffering=0)
    return subprocess.Popen(
        args,
        cwd=cwd,
        stdout=f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=build_env(),
        creationflags=flags,
        close_fds=True,
    )


def launch(name, script_dir, script, logfile):
    """拉起常驻服务。

    ★ 关键：DETACHED_PROCESS 只脱离「控制台」，**脱不出 job object**。
      沙箱/工具用 job 限制时，父 job 一关，子进程就被连带杀掉 —— 表现为
      「服务起来 40 秒就自己没了、日志无任何异常、端口变 closed」。
      所以优先带 CREATE_BREAKAWAY_FROM_JOB（前提是 job 允许 breakaway），
      失败再退回普通 detached。
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, logfile)
    args = [VENV_PY, script]
    base = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    plan = [
        ("breakaway", base | CREATE_BREAKAWAY_FROM_JOB),
        ("plain", base),
    ]
    for tag, flags in plan:
        try:
            p = _popen_detached(args, script_dir, log_path, flags)
        except OSError as e:
            print("  [warn] %s 以 %s 方式启动失败: %s" % (name, tag, e))
            continue
        print("  [ok] %s 已拉起 pid=%d (%s) -> %s" % (name, p.pid, tag, log_path))
        return p.pid
    print("  [err] %s 两种方式均失败" % name)
    return None


def launch_wmi(name, script_dir, script, logfile):
    """用 WMI 拉起（本机唯一能真正常驻的方式，详见 tools/_wmi_spawn.py 注释）。"""
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, logfile)
    spawner = os.path.join(HERE, "_wmi_spawn.py")
    args = [
        VENV_PY,
        spawner,
        "--cwd", script_dir,
        "--log", log_path,
        "--py", VENV_PY,
        "--script", script,
        "--ffmpeg", FFMPEG_BIN,
    ]
    r = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=build_env(),
    )
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    for line in out.splitlines():
        print("  " + line)
    return out


def launch_service(name, script_dir, script, logfile):
    """拉起服务进程（DETACHED_PROCESS）。

    ★ 本机实测（2026-10-02）三条路只有一条通：
      · DETACHED_PROCESS          → 服务起得来，但**当调用方处于 kill-on-close 的 job**
                                    里时，job 一关就被连带杀（表现为"起来 40s 就没了、
                                    日志无异常、端口变 closed"）。用户自己的 cmd / 双击
                                    脚本启动时没有这个问题。
      · CREATE_BREAKAWAY_FROM_JOB → [WinError 5] 沙箱 job 不允许 breakaway
      · WMI Win32_Process.Create  → proc.Create 被 pywin32 解析成 HRESULT；wmic.exe
                                    被沙箱列入程序黑名单 —— 都不可用
      结论：**AI 会话里要启动常驻服务，必须用 --explorer 模式**（借 explorer.exe 脱离 job）
    """
    return launch(name, script_dir, script, logfile)


def write_and_run_explorer_launcher():
    """写一个 .cmd 并借 explorer.exe 拉起它。

    explorer.exe 是登录时就存在的 shell 进程，**不在本会话的 job 里**，
    它拉起的子进程自然也不受"回合回收"影响。本机实测：心跳跨命令持续增长。
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    cmd_path = os.path.join(HERE, "_svc_up.cmd")
    log_path = os.path.join(LOG_DIR, "launcher.out.log")
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "set PYTHONUTF8=1",
        "set PYTHONIOENCODING=utf-8",
        '"%s" "%%~dp0start_services.py" --inner --wait 0 > "%s" 2>&1'
        % (sys.executable, log_path),
    ]
    with open(cmd_path, "wb") as f:
        f.write(("\r\n".join(lines) + "\r\n").encode("utf-8"))
    try:
        subprocess.run(["explorer.exe", cmd_path], capture_output=True, timeout=20)
    except Exception as e:
        print("  [err] explorer 拉起失败: %s" % e)
        return None, log_path
    return cmd_path, log_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--stop", action="store_true")
    ap.add_argument("--wait", type=int, default=40, help="启动后等待秒数")
    ap.add_argument(
        "--explorer", action="store_true",
        help="借 explorer.exe 启动（脱离本会话 job，AI 会话里必须用这个）",
    )
    ap.add_argument("--inner", action="store_true", help="内部使用：真正拉起子进程")
    args = ap.parse_args()

    if args.status:
        status()
        return

    if args.explorer:
        cmd_path, log_path = write_and_run_explorer_launcher()
        if not cmd_path:
            return
        print("已借 explorer 拉起: %s" % cmd_path)
        print("=== 等待 %ds 后复查 ===" % args.wait)
        for _ in range(args.wait):
            time.sleep(1)
        status()
        if os.path.exists(log_path):
            print("=== 内部启动日志 ===")
            with open(log_path, "rb") as fh:
                print(fh.read()[-2500:].decode("utf-8", "replace"))
        return

    if args.stop:
        st = os.path.join(LOG_DIR, "pids.json")
        if os.path.exists(st):
            pids = json.load(open(st, encoding="utf-8"))
            for name, pid in pids.items():
                os.system('taskkill /PID %s /T /F >nul 2>&1' % pid)
                print("  [stop] %s pid=%s" % (name, pid))
        else:
            print("  无 pid 记录")
        time.sleep(1)
        status()
        return

    print("=== 启动前 ===")
    before = status()

    print("=== 拉起服务 ===")
    pids = {}
    if not before.get(8000):
        pids["xiaozhi-server"] = launch_service(
            "xiaozhi-server", SRV_DIR, "app.py", "server.out.log"
        )
    else:
        print("  [skip] 8000 已在监听，跳过 xiaozhi-server")
    if not before.get(8005):
        pids["admin"] = launch_service(
            "admin", ADMIN_DIR, "admin_server.py", "admin.out.log"
        )
    else:
        print("  [skip] 8005 已在监听，跳过 admin")

    st = os.path.join(LOG_DIR, "pids.json")
    if pids:
        old = {}
        if os.path.exists(st):
            try:
                old = json.load(open(st, encoding="utf-8"))
            except Exception:
                old = {}
        old.update(pids)
        json.dump(old, open(st, "w", encoding="utf-8"), indent=2)

    print("=== 等待 %ds 后复查 ===" % args.wait)
    for i in range(args.wait):
        time.sleep(1)
    after = status()

    ok = after.get(8000) and after.get(8003) and after.get(8004)
    print("=== 结果 ===")
    print("  核心服务(8000/8003/8004): %s" % ("全部就绪 ✅" if ok else "未全部就绪 ❌"))
    print("  管理台(8005): %s" % ("就绪 ✅" if after.get(8005) else "未就绪 ⚠️"))
    if not ok:
        lg = os.path.join(LOG_DIR, "server.out.log")
        if os.path.exists(lg):
            print("=== server 日志尾部 ===")
            with open(lg, "rb") as fh:
                data = fh.read()[-3000:]
            print(data.decode("utf-8", "replace"))


if __name__ == "__main__":
    main()
