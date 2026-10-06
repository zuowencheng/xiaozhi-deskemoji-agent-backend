# -*- coding: utf-8 -*-
"""Dump 酷狗窗口的 UIA 控件树，找"第一条搜索结果"该点什么。"""
import sys
from pathlib import Path

SRV = Path(r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\server\xiaozhi-server")
sys.path.insert(0, str(SRV))

import uiautomation as auto  # noqa: E402
from core import pc_music as pm  # noqa: E402

out = []


def walk(ctrl, depth=0, max_depth=18):
    if depth > max_depth:
        return
    try:
        kids = ctrl.GetChildren()
    except Exception:
        return
    for k in kids:
        try:
            ct = k.ControlTypeName
            nm = (k.Name or "").replace("\n", " ")[:60]
            cls = k.ClassName or ""
            rect = k.BoundingRectangle
            out.append("%s%s | name=%r | class=%s | rect=(%d,%d,%d,%d)" % (
                "  " * depth, ct, nm, cls, rect.left, rect.top, rect.right, rect.bottom))
        except Exception as e:
            out.append("%s<?> %s" % ("  " * depth, e))
            continue
        if len(out) > 1600:
            return
        walk(k, depth + 1, max_depth)


win = None
for kw in ("酷狗", "KuGou", "kugou"):
    w = auto.WindowControl(searchDepth=1, RegexName=kw)
    if w.Exists(1, 0.3):
        win = w
        break
if win is None:
    print("没找到酷狗窗口")
    sys.exit(1)

out.append("WINDOW: name=%r class=%s" % (win.Name, win.ClassName))
walk(win)
Path("_kugou_uia_dump.txt").write_text("\n".join(out), encoding="utf-8")
print("已写出 %d 行 -> _kugou_uia_dump.txt" % len(out))
