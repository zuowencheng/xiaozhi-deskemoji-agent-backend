# -*- coding: utf-8 -*-
"""纯 ctypes 抓全屏 → BMP（不依赖 pywin32/Pillow）。"""
import ctypes
import ctypes.wintypes as wt
import os
import struct

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

w = user32.GetSystemMetrics(SM_CXSCREEN)
h = user32.GetSystemMetrics(SM_CYSCREEN)

hdc = user32.GetDC(0)
memdc = gdi32.CreateCompatibleDC(hdc)
hbmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
gdi32.SelectObject(memdc, hbmp)
gdi32.BitBlt(memdc, 0, 0, w, h, hdc, 0, 0, SRCCOPY)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


bi = BITMAPINFOHEADER()
bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
bi.biWidth = w
bi.biHeight = -h          # 负高度 = top-down
bi.biPlanes = 1
bi.biBitCount = 24
bi.biCompression = 0

stride = ((w * 3 + 3) // 4) * 4
buf = ctypes.create_string_buffer(stride * h)
gdi32.GetDIBits(memdc, hbmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)

gdi32.DeleteObject(hbmp)
gdi32.DeleteDC(memdc)
user32.ReleaseDC(0, hdc)

path = os.path.join(os.environ.get("TEMP", "."), "kugou_screen.bmp")
filesize = 14 + 40 + len(buf)
with open(path, "wb") as f:
    f.write(b"BM")
    f.write(struct.pack("<IHHI", filesize, 0, 0, 54))
    f.write(struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(buf), 2835, 2835, 0, 0))
    f.write(buf.raw)

print("saved", path, w, "x", h, os.path.getsize(path), "bytes")
