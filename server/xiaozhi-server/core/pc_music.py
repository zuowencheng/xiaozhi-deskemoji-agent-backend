# -*- coding: utf-8 -*-
"""
pc_music · 让小智"唤起"本机的酷狗音乐（极简版）
================================================

设计取舍（2026-10-03 主人拍板）
------------------------------
主人要的其实很简单：**"帮我把酷狗打开"就行，剩下的我自己点**。
所以这里只做两件事：

1. **唤起酷狗** `open_player()`
   - 已经在跑 → 把窗口还原 + 切到前台（不抢键盘、不模拟输入）
   - 没在跑   → 启动它
   用 ctypes 调 Win32（EnumWindows/ShowWindow/SetForegroundWindow），零第三方依赖。

2. **传输控制** `transport(action)`
   - 播放/暂停、上一首、下一首、停止 —— 走系统级「媒体键」。
     任何注册成媒体会话的播放器（酷狗/网易云/QQ音乐/浏览器视频）都吃这套，
     零依赖、不抢焦点、不用定位界面元素，最稳。

以及一个只读的 `now_playing()`（读系统媒体会话的当前曲目，可选）。

**刻意不做**：UI Automation 搜索点歌。酷狗 20.x 是 CEF 内核，
远程调试端口被它在代码里锁死，界面元素定位脆弱、还抢焦点，
性价比极低 —— 按主人的意思，唤起就够了。

设计基调：**任何一步失败都不抛异常**，只返回一句人话，方便语音侧直接播报。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import time
from pathlib import Path

try:
    from config.logger import setup_logging
except Exception:  # 允许脱离服务端单独调试
    import logging

    def setup_logging():
        logging.basicConfig(level=logging.INFO)
        return logging.getLogger("pc_music")

TAG = "pc_music"
logger = setup_logging()

# ---------------------------------------------------------------- 酷狗定位
_KUGOU_CANDIDATES = [
    r"C:\Software\kugou\KGMusic\KuGou.exe",
    r"C:\Program Files\KuGou\KGMusic\KuGou.exe",
    r"C:\Program Files (x86)\KuGou\KGMusic\KuGou.exe",
]
_KUGOU_PROC = "KuGou.exe"
# 窗口标题里出现这些字样就当它是酷狗（名字/主题可能变，所以多给几个）
_KUGOU_TITLE_KEYS = ("酷狗", "KuGou", "kugou", "KGMusic")


def find_kugou() -> str | None:
    for p in _KUGOU_CANDIDATES:
        if os.path.exists(p):
            return p
    for root in (r"C:\Software", r"C:\Program Files", r"C:\Program Files (x86)"):
        try:
            for p in Path(root).rglob("KuGou.exe"):
                return str(p)
        except Exception:
            continue
    return None


def kugou_running() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq " + _KUGOU_PROC, "/NH"],
            capture_output=True, text=True, timeout=15,
            encoding="gbk", errors="replace",
        ).stdout or ""
        return _KUGOU_PROC.lower() in out.lower()
    except Exception:
        return False


# ---------------------------------------------------------------- Win32 窗口操作
_user32 = ctypes.windll.user32 if os.name == "nt" else None
SW_RESTORE = 9
SW_SHOW = 5

_WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


def _visible_windows():
    """枚举所有可见的顶层窗口，返回 [(hwnd, title)]。"""
    if _user32 is None:
        return []
    result = []

    def _cb(hwnd, _lparam):
        try:
            if not _user32.IsWindowVisible(hwnd):
                return True
            n = _user32.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            _user32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value:
                result.append((hwnd, buf.value))
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    except Exception as e:
        logger.info("[pc_music] EnumWindows 失败: %s", e)
    return result


def _find_kugou_window():
    """找酷狗主窗口 hwnd（标题匹配 + 优先有长度的窗口）。"""
    for hwnd, title in _visible_windows():
        if any(k in title for k in _KUGOU_TITLE_KEYS):
            return hwnd, title
    return None, ""


def _activate(hwnd) -> bool:
    """还原并切到前台。"""
    if _user32 is None or not hwnd:
        return False
    try:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            _user32.ShowWindow(hwnd, SW_SHOW)
        # 前台锁（Foreground Lock）会拦 SetForegroundWindow，用 AttachThreadInput 绕一下
        try:
            fg = _user32.GetForegroundWindow()
            tid_fg = _user32.GetWindowThreadProcessId(fg, None)
            tid_me = ctypes.windll.kernel32.GetCurrentThreadId()
            if tid_fg and tid_fg != tid_me:
                _user32.AttachThreadInput(tid_me, tid_fg, True)
                _user32.SetForegroundWindow(hwnd)
                _user32.AttachThreadInput(tid_me, tid_fg, False)
            else:
                _user32.SetForegroundWindow(hwnd)
        except Exception:
            _user32.SetForegroundWindow(hwnd)
        _user32.BringWindowToTop(hwnd)
        return True
    except Exception as e:
        logger.info("[pc_music] 激活窗口失败: %s", e)
        return False


# ---------------------------------------------------------------- 主功能：唤起
def open_player(wait_seconds: float = 6.0) -> str:
    """唤起酷狗：没开就启动，开了就切到前台。返回一句人话。"""
    # 1) 已经在跑 → 直接切前台
    if kugou_running():
        for _ in range(10):
            hwnd, title = _find_kugou_window()
            if hwnd:
                if _activate(hwnd):
                    logger.info("[pc_music] 酷狗已在前台: %s", title)
                    return "酷狗已经开到前面来了，主人直接点歌就行。"
                break
            time.sleep(0.3)
        # 进程在但窗口没枚举到（可能在托盘/还没画出来）
        return "酷狗在运行，但没找到它的窗口，可能被最小化到托盘了，主人点一下托盘图标试试。"

    # 2) 没在跑 → 启动
    exe = find_kugou()
    if not exe:
        return "没找到酷狗音乐，请先安装，或者告诉我它的安装路径。"
    try:
        os.startfile(exe)
    except Exception as e:
        return f"启动酷狗失败：{e}"

    deadline = time.time() + max(1.0, float(wait_seconds))
    while time.time() < deadline:
        hwnd, title = _find_kugou_window()
        if hwnd:
            _activate(hwnd)
            logger.info("[pc_music] 酷狗已启动并置前")
            return "酷狗已经帮主人打开了，想听什么直接点就行。"
        time.sleep(0.4)

    logger.info("[pc_music] 酷狗进程已拉起，窗口暂未就绪")
    return "酷狗已经在启动了，稍等它把界面画出来，主人直接操作就好。"


# ---------------------------------------------------------------- 传输控制（媒体键）
_VK = {
    "next": 0xB0,
    "prev": 0xB1,
    "stop": 0xB2,
    "play_pause": 0xB3,
}
_KEYEVENTF_KEYUP = 0x0002


def _media_key(vk: int) -> None:
    u = ctypes.windll.user32
    u.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    u.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)


def transport(action: str) -> str:
    """播放/暂停/上一首/下一首/停止 —— 走系统媒体键。"""
    a = (action or "").strip().lower()
    alias = {
        "pause": "play_pause", "resume": "play_pause", "toggle": "play_pause",
        "播放": "play_pause", "暂停": "play_pause", "继续": "play_pause",
        "下一首": "next", "next_track": "next", "skip": "next",
        "上一首": "prev", "previous": "prev", "prev_track": "prev",
        "停止": "stop",
    }
    a = alias.get(a, a)
    if a not in _VK:
        return "不认识这个播放指令（支持：播放/暂停、上一首、下一首、停止）。"
    try:
        _media_key(_VK[a])
    except Exception as e:
        return f"媒体键发送失败：{e}"
    label = {"next": "下一首", "prev": "上一首", "stop": "停止", "play_pause": "播放/暂停"}[a]
    return f"已发送{label}指令。"


# ---------------------------------------------------------------- 当前播放（可选）
def now_playing() -> str:
    """读系统媒体会话的当前曲目（需要 winsdk）。读不到就返回提示。"""
    try:
        from winsdk.windows.media.control import (  # type: ignore
            GlobalSystemMediaTransportControlsSessionManager as _Mgr,
        )
    except Exception:
        return "读不到当前播放信息（未安装 winsdk，可 pip install winsdk）。"

    async def _read():
        mgr = await _Mgr.request_async()
        sess = mgr.get_current_session()
        if sess is None:
            return None
        return await sess.try_get_media_properties_async()

    try:
        import asyncio

        info = asyncio.run(_read())
        if info is None:
            return "当前没有正在播放的媒体。"
        title = info.title or ""
        artist = info.artist or ""
        return (f"正在播放：{artist} - {title}") if artist else (f"正在播放：{title}")
    except Exception as e:
        return f"读当前播放失败：{e}"


# ---------------------------------------------------------------- 自测入口
if __name__ == "__main__":
    import sys

    cmd = sys.argv[1] if len(sys.argv) > 1 else "info"
    if cmd == "info":
        print("酷狗路径:", find_kugou())
        print("酷狗在跑:", kugou_running())
        hwnd, title = _find_kugou_window()
        print("酷狗窗口:", hex(hwnd) if hwnd else None, title)
        print("当前播放:", now_playing())
    elif cmd == "open":
        print(open_player())
    elif cmd == "transport":
        print(transport(sys.argv[2] if len(sys.argv) > 2 else "play_pause"))
    elif cmd == "now":
        print(now_playing())
    else:
        print("用法: python pc_music.py [info|open|now|transport <next|prev|play_pause|stop>]")
