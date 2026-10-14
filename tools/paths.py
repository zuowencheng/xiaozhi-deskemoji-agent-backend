# -*- coding: utf-8 -*-
"""
小智 · 统一路径解析（自发现 + 可覆盖）
============================================================
目的：换台电脑 / 换目录 / 换用户名 / 换 Python 安装位置都能自动找到，
      不再把 `C:\\Users\\xxx\\python.exe` 写死在脚本里。

解析优先级（高 → 低）
------------------------------------------------------------
  ROOT     项目根（tools/ 的父目录）
     1. 环境变量 XIAOZHI_ROOT
     2. 由本文件位置自动推导

  VENV_PY  跑服务（app.py / admin_server.py）的解释器
     1. 环境变量 XIAOZHI_VENV_PY
     2. 本地配置 <ROOT>/xiaozhi.local.json 的 "venv_py"
     3. <ROOT>/server/.venv/Scripts/python.exe   （Windows）
        <ROOT>/server/.venv/bin/python           （POSIX）
     4. <ROOT>/.venv/... 或 <ROOT>/venv/...
     5. 兜底：当前解释器 SELF_PY

  SELF_PY  正在运行本模块的解释器 = sys.executable
            （谁 import paths.py，就是谁在跑）

  BASE_PY  自举用（建 venv / 跑 doctor / 跑部署脚本），默认同 SELF_PY
     1. 环境变量 XIAOZHI_BASE_PY
     2. 本地配置 "base_py"
     3. SELF_PY

  PYW      静默自启用（无控制台窗口）
     1. 环境变量 XIAOZHI_PYTHONW
     2. 本地配置 "pythonw"
     3. SELF_PY 同目录的 pythonw.exe
     4. VENV_PY 同目录的 pythonw.exe
     5. PATH 上的 pythonw

  FFMPEG_BIN  ffmpeg 可执行文件所在目录（可选）
     1. 环境变量 XIAOZHI_FFMPEG_BIN
     2. 本地配置 "ffmpeg_bin"
     3. PATH 上 ffmpeg 所在目录
     4. <ROOT>/runtime/ffmpeg/bin
     → 找不到返回 None（不注入 PATH，不影响启动）

用法
------------------------------------------------------------
    import paths                       # tools/ 在 sys.path 时
    paths.VENV_PY, paths.PYW, paths.FFMPEG_BIN, paths.ROOT
    print(paths.describe())            # 排查用：打印每个值来自哪里

命令行
------------------------------------------------------------
    python tools/paths.py              # 打印解析结果
    python tools/paths.py --write-local  # 把当前结果写进 xiaozhi.local.json
"""
import json
import os
import shutil
import sys

IS_WINDOWS = os.name == "nt"

# ---------------- ROOT ----------------
_THIS = os.path.abspath(__file__)
_TOOLS_DIR = os.path.dirname(_THIS)
_DEFAULT_ROOT = os.path.dirname(_TOOLS_DIR)

ROOT = os.path.abspath(os.environ.get("XIAOZHI_ROOT") or _DEFAULT_ROOT)
TOOLS_DIR = os.path.join(ROOT, "tools")

LOCAL_CFG = os.path.join(ROOT, "xiaozhi.local.json")


# ---------------- 本地配置（不进 git） ----------------
def _load_local():
    try:
        with open(LOCAL_CFG, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_CFG = _load_local()


def _venv_python_in(base):
    """给一个 venv 目录，返回其 python 可执行文件路径（不存在则 None）。"""
    cands = (
        [os.path.join(base, "Scripts", "python.exe"),
         os.path.join(base, "bin", "python")]
        if IS_WINDOWS else
        [os.path.join(base, "bin", "python3"),
         os.path.join(base, "bin", "python")]
    )
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def _sibling_pythonw(py):
    """与 py 同目录的 pythonw（Windows 静默解释器）；找不到返回 None。"""
    if not py:
        return None
    if not IS_WINDOWS:
        return None
    w = os.path.join(os.path.dirname(py), "pythonw.exe")
    return w if os.path.isfile(w) else None


# ---------------- SELF_PY ----------------
def _resolve_self_py():
    exe = sys.executable
    if exe and os.path.isfile(exe):
        return exe
    return None


SELF_PY = _resolve_self_py()


# ---------------- BASE_PY ----------------
def _resolve_base_py():
    v = os.environ.get("XIAOZHI_BASE_PY")
    if v and os.path.isfile(v):
        return v
    v = _CFG.get("base_py")
    if v and os.path.isfile(v):
        return v
    if SELF_PY:
        return SELF_PY
    # 最后手段：PATH 上的 python
    for name in (("python", "python3") if not IS_WINDOWS else ("python", "python3")):
        found = shutil.which(name)
        if found:
            return found
    return None


BASE_PY = _resolve_base_py()


# ---------------- VENV_PY ----------------
def _resolve_venv_py():
    v = os.environ.get("XIAOZHI_VENV_PY")
    if v and os.path.isfile(v):
        return v
    v = _CFG.get("venv_py")
    if v and os.path.isfile(v):
        return v
    for base in (
        os.path.join(ROOT, "server", ".venv"),
        os.path.join(ROOT, ".venv"),
        os.path.join(ROOT, "venv"),
    ):
        found = _venv_python_in(base)
        if found:
            return found
    # 兜底：当前解释器（可能已经是带依赖的 venv）
    return SELF_PY


VENV_PY = _resolve_venv_py()


# ---------------- PYW ----------------
def _resolve_pyw():
    v = os.environ.get("XIAOZHI_PYTHONW")
    if v and os.path.isfile(v):
        return v
    v = _CFG.get("pythonw")
    if v and os.path.isfile(v):
        return v
    for py in (SELF_PY, VENV_PY, BASE_PY):
        found = _sibling_pythonw(py)
        if found:
            return found
    found = shutil.which("pythonw")
    return found


PYW = _resolve_pyw()
# 拿不到 pythonw 时退回普通解释器（会有控制台窗口，但功能不丢）
PYW_OR_PY = PYW or VENV_PY or BASE_PY or "python"


# ---------------- FFMPEG_BIN ----------------
def _resolve_ffmpeg_bin():
    v = os.environ.get("XIAOZHI_FFMPEG_BIN")
    if v and os.path.isdir(v):
        return v
    v = _CFG.get("ffmpeg_bin")
    if v and os.path.isdir(v):
        return v
    exe = shutil.which("ffmpeg")
    if exe:
        return os.path.dirname(exe)
    cand = os.path.join(ROOT, "runtime", "ffmpeg", "bin")
    return cand if os.path.isdir(cand) else None


FFMPEG_BIN = _resolve_ffmpeg_bin()


# ---------------- 便捷常量 ----------------
SRV_DIR = os.path.join(ROOT, "server", "xiaozhi-server")
ADMIN_DIR = os.path.join(ROOT, "server", "admin")
LOG_DIR = os.path.join(ROOT, "server", "_logs")
BRIDGE_DIR = os.path.join(ROOT, "bridge")


# ---------------- 排查 / 导出 ----------------
def _src_of(name, value):
    if value is None:
        return "（未找到）"
    if os.environ.get(name):
        return "环境变量"
    key = {"XIAOZHI_VENV_PY": "venv_py", "XIAOZHI_BASE_PY": "base_py",
           "XIAOZHI_PYTHONW": "pythonw", "XIAOZHI_FFMPEG_BIN": "ffmpeg_bin"}.get(name, "")
    if key and _CFG.get(key):
        return "本地配置"
    return "自动发现"


def describe():
    lines = [
        "小智 · 路径解析结果",
        "  ROOT      = %s" % ROOT,
        "  SELF_PY   = %s" % SELF_PY,
        "  BASE_PY   = %s" % BASE_PY,
        "  VENV_PY   = %s   [%s]" % (VENV_PY, _src_of("XIAOZHI_VENV_PY", VENV_PY)),
        "  PYW       = %s   [%s]" % (PYW, _src_of("XIAOZHI_PYTHONW", PYW)),
        "  FFMPEG_BIN= %s   [%s]" % (FFMPEG_BIN, _src_of("XIAOZHI_FFMPEG_BIN", FFMPEG_BIN)),
        "  本地配置   = %s %s" % (LOCAL_CFG, "(存在)" if os.path.isfile(LOCAL_CFG) else "(无)"),
    ]
    return "\n".join(lines)


def write_local_config():
    """把当前解析结果写进 xiaozhi.local.json（本机固定用，不进 git）。"""
    data = {}
    if VENV_PY:
        data["venv_py"] = VENV_PY
    if PYW:
        data["pythonw"] = PYW
    if BASE_PY:
        data["base_py"] = BASE_PY
    if FFMPEG_BIN:
        data["ffmpeg_bin"] = FFMPEG_BIN
    data["_readme"] = "本机路径固定配置（不进 git）。删掉即可回到自动发现。"
    with open(LOCAL_CFG, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return LOCAL_CFG


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if "--write-local" in sys.argv:
        p = write_local_config()
        print("已写入: %s" % p)
        print()
    print(describe())
