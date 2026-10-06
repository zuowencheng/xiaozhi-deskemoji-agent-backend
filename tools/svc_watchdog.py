# -*- coding: utf-8 -*-
"""
小智后台 · 看门狗（常驻自愈）
------------------------------------------------------------
职责：**只做一件事** —— 盯着 8000(app.py) 与 8005(admin) 两个端口，
      谁不监听就把谁拉起来。它自己由「任务计划程序(登录时)」启动，
      因此在会话/沙箱之外，拉起的子进程不会被回合回收。

设计要点：
  · 单实例：命名互斥体，重复启动直接退出（防止多个看门狗互抢）。
  · 两次确认：连续 2 次探测失败才认定挂了，避免抖动误判。
  · 冷却期：拉起后 GRACE 秒内不再重复拉起，避免端口 TIME_WAIT 误判。
  · 不猜密码、不杀进程：只负责「端口不通 → 拉起」，重启进程交给用户/管理台。

用法：
    pythonw tools/svc_watchdog.py          # 常驻（无窗口）
    python  tools/svc_watchdog.py --once   # 跑一轮就退（自检用）
"""
import argparse
import ctypes
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

# ---------------- 路径 / 常量 ----------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRV_DIR = os.path.join(ROOT, "server", "xiaozhi-server")
ADMIN_DIR = os.path.join(ROOT, "server", "admin")
LOG_DIR = os.path.join(ROOT, "server", "_logs")
LOG_PATH = os.path.join(LOG_DIR, "watchdog.log")

VENV_PY = r"C:\Users\zuowe\.workbuddy\binaries\python\envs\xiaozhi-server\Scripts\python.exe"
FFMPEG_BIN = r"C:\Users\zuowe\.workbuddy\binaries\ffmpeg\ffmpeg-master-latest-win64-gpl\bin"

CHECK_INTERVAL = 20          # 巡检间隔（秒）
GRACE = 40                   # 拉起后冷却期（秒）
RETRY_CONFIRM = 2            # 连续失败几次才认定挂了
LOG_MAX = 2 * 1024 * 1024    # 日志超过 2MB 轮转

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# 每个服务：端口 / 工作目录 / 启动命令
SERVICES = [
    {
        "name": "xiaozhi-server",
        "port": 8000,
        "cwd": SRV_DIR,
        "args": ["app.py"],
        "log": "server.out.log",
        "err": "server.err.log",
    },
    {
        "name": "admin",
        "port": 8005,
        "cwd": ADMIN_DIR,
        "args": ["admin_server.py", "8005"],
        "log": "admin.out.log",
        "err": "admin.err.log",
    },
]


# ---------------- 基础工具 ----------------
def _log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    line = "%s  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)


def _rotate_log():
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX:
            bak = LOG_PATH + ".1"
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(LOG_PATH, bak)
    except OSError:
        pass


def port_open(port, host="127.0.0.1", timeout=1.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def build_env():
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if os.path.isdir(FFMPEG_BIN):
        env["PATH"] = FFMPEG_BIN + os.pathsep + env.get("PATH", "")
    env.pop("MSYSTEM", None)
    env.pop("MINGW_PREFIX", None)
    return env


def single_instance():
    """命名互斥体；已有实例在跑则返回 False。"""
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "XiaozhiSvcWatchdogMutex")
    if not handle:
        return True  # 拿不到句柄就放行，别因互斥体本身坏掉而不干活
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return False
    # 句柄故意不释放：进程活着期间一直持有
    return True


def spawn(svc):
    """拉起一个服务；返回 (ok, detail)。"""
    os.makedirs(LOG_DIR, exist_ok=True)
    out_path = os.path.join(LOG_DIR, svc["log"])
    err_path = os.path.join(LOG_DIR, svc["err"])
    cmd = [VENV_PY] + svc["args"]
    base = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    for tag, flags in (("breakaway", base | CREATE_BREAKAWAY_FROM_JOB), ("plain", base)):
        try:
            fout = open(out_path, "a", encoding="utf-8", errors="replace")
            ferr = open(err_path, "a", encoding="utf-8", errors="replace")
            p = subprocess.Popen(
                cmd,
                cwd=svc["cwd"],
                env=build_env(),
                stdout=fout,
                stderr=ferr,
                stdin=subprocess.DEVNULL,
                creationflags=flags,
                close_fds=True,
            )
            return True, "pid=%d (%s)" % (p.pid, tag)
        except OSError as e:
            continue
    return False, "两种方式均失败"


# ---------------- 主循环 ----------------
def run_once(state):
    """巡检一轮。state 保存 last_spawn / fail_count。"""
    for svc in SERVICES:
        name = svc["name"]
        if port_open(svc["port"]):
            if state["fail"].get(name):
                _log("[恢复] %s(%d) 已恢复监听" % (name, svc["port"]))
            state["fail"][name] = 0
            continue

        # 端口不通
        n = state["fail"].get(name, 0) + 1
        state["fail"][name] = n
        if n < RETRY_CONFIRM:
            _log("[待定] %s(%d) 第 %d 次探测失败，等待复检" % (name, svc["port"], n))
            continue

        now = time.time()
        if now - state["last"].get(name, 0) < GRACE:
            left = int(GRACE - (now - state["last"][name]))
            _log("[冷却] %s(%d) 仍在冷却期(%ds)，跳过" % (name, svc["port"], left))
            continue

        _log("[拉起] %s(%d) 端口不通，尝试拉起" % (name, svc["port"]))
        ok, detail = spawn(svc)
        _log("        %s -> %s" % ("成功" if ok else "失败", detail))
        state["last"][name] = now
        state["fail"][name] = 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只巡检一轮就退出")
    args = ap.parse_args()

    if not single_instance():
        _log("[跳过] 已有看门狗实例在运行，本进程退出")
        return

    _log("=" * 50)
    _log("看门狗启动 pid=%d，巡检间隔 %ds" % (os.getpid(), CHECK_INTERVAL))

    state = {"last": {}, "fail": {}}
    if args.once:
        run_once(state)
        _log("--once 巡检结束")
        return

    while True:
        try:
            run_once(state)
        except Exception as e:  # 看门狗自己绝不能死
            _log("[异常] %s: %r" % (type(e).__name__, e))
        _rotate_log()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
