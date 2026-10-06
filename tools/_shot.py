# -*- coding: utf-8 -*-
"""用 pywin32 抓全屏存 BMP（供分析酷狗界面坐标）。"""
import os
import sys

try:
    import win32api
    import win32con
    import win32gui
    import win32ui
except Exception as e:
    print("pywin32 不可用:", e)
    sys.exit(1)

out = os.path.join(os.environ.get("TEMP", "."), "kugou_screen.bmp")

w = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
h = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
hdesk = win32gui.GetDesktopWindow()
hwndDC = win32gui.GetWindowDC(hdesk)
srcDC = win32ui.CreateDCFromHandle(hwndDC)
memDC = srcDC.CreateCompatibleDC()
bmp = win32ui.CreateBitmap()
bmp.CreateCompatibleBitmap(srcDC, w, h)
memDC.SelectObject(bmp)
memDC.BitBlt((0, 0), (w, h), srcDC, (0, 0), win32con.SRCCOPY)
bmp.SaveBitmapFile(memDC, out)
print("saved %s %dx%d" % (out, w, h))

# 前台窗口信息
try:
    fg = win32gui.GetForegroundWindow()
    print("foreground:", win32gui.GetWindowText(fg), "| rect", win32gui.GetWindowRect(fg))
except Exception as e:
    print("fg err", e)
