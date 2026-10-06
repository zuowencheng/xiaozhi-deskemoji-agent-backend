# -*- coding: utf-8 -*-
"""试：带 --force-renderer-accessibility 重启酷狗，看 UIA 树是否变丰富。"""
import os
import subprocess
import sys
import time
from pathlib import Path

SRV = Path(r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\server\xiaozhi-server")
sys.path.insert(0, str(SRV))

EXE = r"C:\Software\kugou\KGMusic\KuGou.exe"

# 先杀掉
subprocess.run("taskkill /F /IM KuGou.exe /T", shell=True, capture_output=True, timeout=30)
time.sleep(3)
print("已结束旧进程")

# 带无障碍开关重启
try:
    subprocess.Popen([EXE, "--force-renderer-accessibility"])
    print("已启动（带 --force-renderer-accessibility）")
except Exception as e:
    print("启动失败:", e)
    sys.exit(1)

time.sleep(20)

import uiautomation as auto  # noqa: E402

out = []


def walk(ctrl, depth=0, max_depth=16):
    if depth > max_depth or len(out) > 1400:
        return
    try:
        kids = ctrl.GetChildren()
    except Exception:
        return
    for k in kids:
        try:
            out.append("%s%s | name=%r | rect=(%d,%d,%d,%d)" % (
                "  " * depth, k.ControlTypeName, (k.Name or "")[:60],
                k.BoundingRectangle.left, k.BoundingRectangle.top,
                k.BoundingRectangle.right, k.BoundingRectangle.bottom))
        except Exception:
            continue
        walk(k, depth + 1, max_depth)


win = None
for kw in ("酷狗", "KuGou"):
    w = auto.WindowControl(searchDepth=1, RegexName=kw)
    if w.Exists(2, 0.5):
        win = w
        break
if win is None:
    print("没找到窗口（可能还没起来）")
    sys.exit(1)
walk(win)
Path("_kugou_uia_dump2.txt").write_text("\n".join(out), encoding="utf-8")
named = sum(1 for l in out if "name=''" not in l)
print("dump %d 行，其中有名字的 %d 行" % (len(out), named))
print("样本（有名字的）：")
for l in out:
    if "name=''" not in l:
        print("  " + l.strip()[:120])
