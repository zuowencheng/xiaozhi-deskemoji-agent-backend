# -*- coding: utf-8 -*-
"""纯 ctypes 抓全屏 → PNG（zlib 手写，无需 Pillow）。"""
import ctypes
import ctypes.wintypes as wt
import os
import struct
import zlib

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

w = user32.GetSystemMetrics(0)
h = user32.GetSystemMetrics(1)

hdc = user32.GetDC(0)
memdc = gdi32.CreateCompatibleDC(hdc)
hbmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
gdi32.SelectObject(memdc, hbmp)
gdi32.BitBlt(memdc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020)


class BIH(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 24, 0, 0, 2835, 2835, 0, 0)
stride = ((w * 3 + 3) // 4) * 4
buf = ctypes.create_string_buffer(stride * h)
gdi32.GetDIBits(memdc, hbmp, 0, h, buf, ctypes.byref(bi), 0)
gdi32.DeleteObject(hbmp); gdi32.DeleteDC(memdc); user32.ReleaseDC(0, hdc)

raw = buf.raw
rows = []
for y in range(h):
    row = raw[y * stride:y * stride + w * 3]
    # BGR -> RGB，前面加 filter byte 0
    px = bytearray(w * 3)
    px[0::3] = row[2::3]
    px[1::3] = row[1::3]
    px[2::3] = row[0::3]
    rows.append(b"\x00" + bytes(px))
png = (b"\x89PNG\r\n\x1a\n"
       + b"".join([struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0),
                   struct.pack(">I", len(zlib.compress(b"".join(rows), 6))) + b"IDAT"
                   + zlib.compress(b"".join(rows), 6),
                   struct.pack(">I", 0) + b"IEND"]))

# 修正 CRC
def chunk(t, d):
    c = t + d
    return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)


idat = zlib.compress(b"".join(rows), 6)
png = (b"\x89PNG\r\n\x1a\n"
       + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
       + chunk(b"IDAT", idat)
       + chunk(b"IEND", b""))

path = os.path.join(os.environ.get("TEMP", "."), "kugou_screen.png")
with open(path, "wb") as f:
    f.write(png)
print("saved", path, w, "x", h, os.path.getsize(path), "bytes")
