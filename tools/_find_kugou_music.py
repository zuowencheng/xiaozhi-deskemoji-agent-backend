# -*- coding: utf-8 -*-
"""探测酷狗音乐的下载/缓存目录与本地曲库（只读扫描）。"""
import os
import sys

KUGOU_DATA = r"C:\Users\zuowe\AppData\Roaming\KuGou8"
AUDIO_EXT = {".mp3", ".flac", ".ape", ".wav", ".m4a", ".ogg", ".wma", ".kgm", ".kgg", ".kgma"}

print("=== 1) KuGou.ini 中与路径有关的行 ===")
ini = os.path.join(KUGOU_DATA, "KuGou.ini")
try:
    t = open(ini, "rb").read().decode("gbk", errors="replace")
    for line in t.splitlines():
        ll = line.lower()
        if any(k in ll for k in ("path", "dir", "down", "cache", "save", "folder", "download")):
            print("   ", line.strip()[:170])
except Exception as e:
    print("   读取失败:", e)

print()
print("=== 2) KuGou8 数据目录一级结构 ===")
try:
    for n in sorted(os.listdir(KUGOU_DATA)):
        p = os.path.join(KUGOU_DATA, n)
        if os.path.isdir(p):
            try:
                cnt = sum(1 for _ in os.scandir(p))
            except Exception:
                cnt = -1
            print("    DIR ", n, "(entries=%d)" % cnt)
except Exception as e:
    print("   失败:", e)

print()
print("=== 3) 全盘浅层搜索：含 kugou/kgmusic 的目录 + 音频文件 ===")
roots = [r"C:\Users\zuowe", r"C:\Software", r"D:\\", r"E:\\"]
SKIP = {"node_modules", ".git", "windows", "program files", "program files (x86)",
        "appdata\\local\\temp", "$recycle.bin", "system volume information"}

hits_dirs = []
hits_audio = []
for root in roots:
    if not os.path.isdir(root):
        continue
    base = root.rstrip("\\").count(os.sep)
    for dp, dns, fns in os.walk(root, topdown=True):
        depth = dp.count(os.sep) - base
        if depth > 4:
            dns[:] = []
            continue
        dns[:] = [d for d in dns if d.lower() not in SKIP and not d.startswith("$")]
        low = os.path.basename(dp).lower()
        if "kugou" in low or "kgmusic" in low or "kgcache" in low or "kgdownload" in low:
            hits_dirs.append(dp)
        for fn in fns:
            ext = os.path.splitext(fn)[1].lower()
            if ext in AUDIO_EXT:
                fp = os.path.join(dp, fn)
                try:
                    sz = os.path.getsize(fp)
                except Exception:
                    sz = 0
                hits_audio.append((fp, sz))

print("  -- 目录命中 --")
for d in hits_dirs:
    print("    ", d)
    try:
        for n in sorted(os.listdir(d))[:20]:
            print("         |", n)
    except Exception:
        pass

print("  -- 音频文件命中（前 80 条）--")
for fp, sz in hits_audio[:80]:
    print("    %9d  %s" % (sz, fp))
print("  音频文件总数:", len(hits_audio))
