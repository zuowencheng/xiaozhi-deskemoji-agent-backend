# -*- coding: utf-8 -*-
"""
pc_desktop · 桌面操控底层（纯 ctypes，零第三方依赖）
====================================================

给"用嘴控制电脑"补上最后几块拼图。`core/pc_agent.py` 原来只有
跑命令 / 读写文件 / 列目录 / 开路径这些"文件层"能力，缺的是**桌面层**：

    · 窗口：列出 / 置前 / 最小化 / 最大化 / 关闭 / 找标题
    · 剪贴板：读 / 写（复制一段话、粘贴一段话）
    · 键盘：发组合键（Ctrl+C、Alt+Tab、Win+D…）、打字
    · 截图：抓全屏或指定窗口，存 PNG

为什么用 ctypes 而不是 pywin32/uiautomation
-------------------------------------------
本机没装、也不该为这几件事加依赖。user32/gdi32 是 Windows 自带的，
ctypes 直接调，装不装包都能跑，也不会因为某个包装不上就整条链路瘫掉。

对外接口（供 pc_agent 注册成 LLM 工具）
--------------------------------------
    list_windows()                          -> [{"hwnd","title","visible"}]
    window_action(title, action, hwnd=None) -> {"ok","msg","matched"}
    clipboard_get()                         -> {"ok","text"}
    clipboard_set(text)                     -> {"ok","msg"}
    send_keys(keys, ...)                    -> {"ok","msg"}
    screenshot(save_to, hwnd=None)          -> {"ok","path","size"}

CLI 自检（不开 LLM，直接验证本机能力）
--------------------------------------
    python core/pc_desktop.py windows
    python core/pc_desktop.py clip-get
    python core/pc_desktop.py clip-set "要复制到剪贴板的话"
    python core/pc_desktop.py shot
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# 64 位 Python 上句柄是 8 字节，不声明 restype 会被截成 32 位 int →
# GlobalLock 拿到 0x0 → 写剪贴板直接 access violation。这一组声明不能省。
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.argtypes = [wt.HGLOBAL]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [ctypes.c_uint, wt.HANDLE]   # 不声明会把句柄当 c_int → OverflowError
user32.SetClipboardData.restype = wt.HANDLE
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.GetWindowDC.restype = wt.HDC
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, ctypes.c_uint, ctypes.c_uint,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
# 声明了才知道真实分辨率，否则 DPI 缩放会把 1920x1080 报成 1280x720
try:
    user32.SetProcessDPIAware()
except Exception:
    pass

# --- 常量 ---------------------------------------------------------------
SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE = 6, 3, 9
WM_CLOSE = 0x0010
KEYEVENTF_KEYUP = 0x0002
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# 虚拟键码表（够用就好，别搞成百科全书）
VK = {
    "enter": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "back": 0x08, "backspace": 0x08, "delete": 0x2E,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "win": 0x5B, "ctrl": 0x11, "alt": 0x12, "shift": 0x10,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f11": 0x7A, "f12": 0x7B,
}
VK.update({chr(c): c for c in range(0x30, 0x3A)})          # 0-9
VK.update({chr(c): c for c in range(0x41, 0x5B)})          # A-Z


# --- 窗口 ---------------------------------------------------------------
def _enum_windows():
    """枚举顶层可见窗口，返回 [(hwnd, title)]。"""
    out = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value.strip()
                if title:
                    out.append((int(hwnd), title))
        return True

    user32.EnumWindows(EnumWindowsProc(_cb), 0)
    return out


def list_windows(visible_only: bool = True, limit: int = 40) -> dict:
    wins = _enum_windows()
    items = [
        {"hwnd": h, "title": t, "visible": True,
         "minimized": bool(user32.IsIconic(h))}
        for h, t in wins
    ]
    return {"ok": True, "count": len(items), "windows": items[:limit]}


def _find_window(title: str):
    """按标题模糊匹配，返回 (hwnd, real_title)。找前台也算候选。"""
    title = (title or "").strip().lower()
    if not title:
        return None, None
    wins = _enum_windows()
    # 1) 完全相等优先
    for h, t in wins:
        if t.lower() == title:
            return h, t
    # 2) 包含
    for h, t in wins:
        if title in t.lower():
            return h, t
    # 3) 反过来：窗口标题包含查询词的子串（应对"酷狗"vs"酷狗音乐"）
    for h, t in wins:
        if t.lower() in title:
            return h, t
    return None, None


def window_action(title: str = "", action: str = "focus", hwnd: int = 0) -> dict:
    """对窗口做动作：focus / minimize / maximize / restore / close / show"""
    action = (action or "focus").strip().lower()
    h = int(hwnd or 0)
    matched = ""
    if not h:
        h, matched = _find_window(title)
    if not h:
        return {"ok": False, "msg": f"没找到标题含「{title}」的窗口",
                "available": [t for _, t in _enum_windows()][:15]}

    if not matched:
        n = user32.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, buf, n + 1)
        matched = buf.value

    # 被最小化的窗口要先还原，否则置前无效
    if action in ("focus", "show", "restore", "maximize") and user32.IsIconic(h):
        user32.ShowWindow(h, SW_RESTORE)
        time.sleep(0.15)

    try:
        if action in ("focus", "show"):
            user32.SetForegroundWindow(h)
            msg = f"已把「{matched}」切到前台"
        elif action == "minimize":
            user32.ShowWindow(h, SW_MINIMIZE)
            msg = f"已最小化「{matched}」"
        elif action == "maximize":
            user32.ShowWindow(h, SW_MAXIMIZE)
            user32.SetForegroundWindow(h)
            msg = f"已最大化「{matched}」"
        elif action == "restore":
            user32.ShowWindow(h, SW_RESTORE)
            msg = f"已还原「{matched}」"
        elif action == "close":
            user32.PostMessageW(h, WM_CLOSE, 0, 0)
            msg = f"已请求关闭「{matched}」（程序可能会先弹保存提示）"
        else:
            return {"ok": False, "msg": f"不认识的动作：{action}",
                    "hint": "可选 focus/minimize/maximize/restore/close"}
    except Exception as e:
        return {"ok": False, "msg": f"操作失败：{type(e).__name__}: {e}"}

    return {"ok": True, "msg": msg, "hwnd": h, "matched": matched}


# --- 剪贴板 -------------------------------------------------------------
def clipboard_get() -> dict:
    try:
        user32.OpenClipboard(0)
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return {"ok": True, "text": "", "msg": "剪贴板是空的或不是文本"}
        p = kernel32.GlobalLock(h)
        try:
            text = ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
        return {"ok": True, "text": text, "length": len(text)}
    except Exception as e:
        return {"ok": False, "msg": f"读剪贴板失败：{type(e).__name__}: {e}"}
    finally:
        try:
            user32.CloseClipboard()
        except Exception:
            pass


def clipboard_set(text: str) -> dict:
    text = text or ""
    try:
        user32.OpenClipboard(0)
        user32.EmptyClipboard()
        # +1 留给结尾的 \0
        nbytes = (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar)
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, nbytes)
        p = kernel32.GlobalLock(h)
        try:
            ctypes.memmove(p, text.encode("utf-16-le"), nbytes - 2)
            ctypes.memset(p + nbytes - 2, 0, 2)
        finally:
            kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
        return {"ok": True, "msg": f"已复制到剪贴板（{len(text)} 字）"}
    except Exception as e:
        return {"ok": False, "msg": f"写剪贴板失败：{type(e).__name__}: {e}"}
    finally:
        try:
            user32.CloseClipboard()
        except Exception:
            pass


# --- 键盘 ---------------------------------------------------------------
def _tap(vk: int, down: bool = True):
    user32.keybd_event(vk, 0, 0 if down else KEYEVENTF_KEYUP, 0)


def send_keys(keys: str, delay: float = 0.05, title: str = "") -> dict:
    """
    发组合键，写法："ctrl+c" / "alt+tab" / "win+d" / "enter" / "ctrl+shift+esc"
    也支持直接打字：send_keys("hello") 会逐字符敲。
    """
    keys = (keys or "").strip()
    if not keys:
        return {"ok": False, "msg": "没给我要按的键"}

    if title:
        r = window_action(title, "focus")
        if not r.get("ok"):
            return r
        time.sleep(0.2)

    parts = [p.strip().lower() for p in keys.split("+")] if "+" in keys else [keys.lower()]
    # 组合键：最后一个是主键，前面的是修饰键
    is_combo = len(parts) > 1
    try:
        if is_combo:
            mod_vks, main = [], parts[-1]
            for m in parts[:-1]:
                if m == "ctrl":
                    mod_vks.append(0x11)
                elif m == "alt":
                    mod_vks.append(0x12)
                elif m == "shift":
                    mod_vks.append(0x10)
                elif m == "win":
                    mod_vks.append(0x5B)
                else:
                    return {"ok": False, "msg": f"不认识的修饰键：{m}"}
            if len(main) == 1 and main.isalnum():
                main_vk = VK.get(main.upper(), ord(main.upper()))
            else:
                main_vk = VK.get(main)
                if main_vk is None:
                    return {"ok": False, "msg": f"不认识的按键：{main}"}
            for v in mod_vks:
                _tap(v, True)
            time.sleep(delay)
            _tap(main_vk, True)
            time.sleep(delay)
            _tap(main_vk, False)
            time.sleep(delay)
            for v in reversed(mod_vks):
                _tap(v, False)
            return {"ok": True, "msg": f"已发送组合键 {keys}"}

        # 打字 or 单个键
        for ch in (keys if len(keys) > 1 and " " in keys else [keys]):
            if isinstance(ch, str) and len(ch) == 1:
                vk = VK.get(ch.upper())
                if vk is None:
                    # 非 ASCII（中文等）走剪贴板粘贴，ctypes 没法直接敲
                    clipboard_set(ch)
                    time.sleep(0.05)
                    _tap(0x11, True); _tap(ord("V"), True)
                    _tap(ord("V"), False); _tap(0x11, False)
                else:
                    need_shift = ch.isupper() or ch in '~!@#$%^&*()_+{}|:"<>?'
                    if need_shift:
                        _tap(0x10, True)
                    _tap(vk, True); time.sleep(delay); _tap(vk, False)
                    if need_shift:
                        _tap(0x10, False)
            else:
                vk = VK.get(ch.lower() if isinstance(ch, str) else ch)
                if vk is None:
                    return {"ok": False, "msg": f"不认识的按键：{ch}"}
                _tap(vk, True); time.sleep(delay); _tap(vk, False)
            time.sleep(delay)
        return {"ok": True, "msg": f"已输入：{keys[:40]}"}
    except Exception as e:
        return {"ok": False, "msg": f"按键失败：{type(e).__name__}: {e}"}


# --- 桌面刷新 -----------------------------------------------------------
# 主人实测：让小智删掉桌面上的截图，文件确实没了，但**资源管理器还显示在**，
# 必须手动按 F5 才消失。删完没"消失感"，体验就像没删成功。
# 这里直接通知资源管理器刷新，比发 F5 更准（只刷文件窗口，不影响别的程序）。
_CABINET_CLASSES = ("CabinetWClass", "ExploreWClass")


def refresh_desktop() -> dict:
    """让所有打开的资源管理器窗口刷新（等效于在里面按 F5）。"""
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _):
        try:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in _CABINET_CLASSES:
                found.append(int(hwnd))
        except Exception:
            pass
        return True

    user32.EnumWindows(_cb, 0)

    if not found:
        # 没开文件窗口就算了，不算失败（主人可能在桌面上直接看）
        return {"ok": True, "msg": "没有打开的资源管理器窗口，无需刷新", "refreshed": 0}

    n = 0
    for h in found:
        try:
            # 只发 F5 键，不抢焦点（PostMessage 到窗口）
            user32.PostMessageW(h, 0x0100, 0x74, 0)  # WM_KEYDOWN, VK_F5
            user32.PostMessageW(h, 0x0101, 0x74, 0)  # WM_KEYUP
            n += 1
        except Exception:
            pass
    return {"ok": True, "msg": f"已刷新 {n} 个资源管理器窗口", "refreshed": n}


# --- 截屏 ---------------------------------------------------------------
def screenshot(save_to: str = "", hwnd: int = 0, title: str = "") -> dict:
    """抓屏存 PNG。默认存桌面，文件名带时间戳。
    纯 GDI + PNG 手写（zlib 标准库），不依赖 Pillow。"""
    import zlib
    import struct

    h = int(hwnd or 0)
    if not h and title:
        h, _ = _find_window(title)
        if not h:
            return {"ok": False, "msg": f"没找到窗口「{title}」"}

    if h:
        r = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        left, top = r.left, r.top
        w, h_ = r.right - r.left, r.bottom - r.top
    else:
        left = top = 0
        w = user32.GetSystemMetrics(0)
        h_ = user32.GetSystemMetrics(1)
    if w <= 0 or h_ <= 0:
        return {"ok": False, "msg": "抓不到有效区域（窗口可能最小化了）"}

    hdc_screen = user32.GetDC(0)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h_)
    gdi32.SelectObject(hdc_mem, bmp)
    SRCCOPY = 0x00CC0020
    gdi32.BitBlt(hdc_mem, 0, 0, w, h_, hdc_screen, left, top, SRCCOPY)

    # 取 BITMAPINFOHEADER + 像素（32bpp BGRA）
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                    ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                    ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                    ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD),
                    ("biClrImportant", wt.DWORD)]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h_          # 负值 = 自上而下，省得翻转
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h_ * 4)
    gdi32.GetDIBits(hdc_mem, bmp, 0, h_, buf, ctypes.byref(bi), 0)

    # 拼 PNG
    raw = bytearray()
    stride = w * 4
    for y in range(h_):
        raw.append(0)  # filter type 0
        raw += buf[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h_, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)

    if not save_to:
        # ★ 2026-10-03 主人要求「桌面建『小智助手过程文档』目录，临时资料都扔这里」。
        #   原来默认直接扔桌面根目录（`小智截屏-*.png`），桌面会被堆满。
        from core.workdir import work_dir
        save_to = str(work_dir("截图") / f"小智截屏-{datetime.now():%Y%m%d-%H%M%S}.png")
    p = Path(save_to)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(png)
    return {"ok": True, "path": str(p), "size": f"{w}x{h_}", "bytes": len(png)}


# --- CLI ----------------------------------------------------------------
def _cli():
    import sys, json
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__); return
    cmd = a[0]
    if cmd == "windows":
        print(json.dumps(list_windows(), ensure_ascii=False, indent=2)[:2000])
    elif cmd == "clip-get":
        print(json.dumps(clipboard_get(), ensure_ascii=False)[:800])
    elif cmd == "clip-set":
        print(json.dumps(clipboard_set(" ".join(a[1:])), ensure_ascii=False))
    elif cmd == "win":
        # win "酷狗" focus
        print(json.dumps(window_action(a[1] if len(a) > 1 else "",
                                       a[2] if len(a) > 2 else "focus"), ensure_ascii=False))
    elif cmd == "keys":
        print(json.dumps(send_keys(a[1] if len(a) > 1 else ""), ensure_ascii=False))
    elif cmd == "shot":
        print(json.dumps(screenshot(a[1] if len(a) > 1 else ""), ensure_ascii=False))
    elif cmd == "refresh":
        print(json.dumps(refresh_desktop(), ensure_ascii=False))
    else:
        print("未知命令，可选：windows / clip-get / clip-set / win / keys / shot")


if __name__ == "__main__":
    _cli()
