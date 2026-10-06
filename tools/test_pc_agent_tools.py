# -*- coding: utf-8 -*-
"""验证 pc_agent 新增工具：search_web / search_files / organize_dir（计划模式）。"""
import os
import sys
import tempfile
from pathlib import Path

SRV = Path(r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\server\xiaozhi-server")
sys.path.insert(0, str(SRV))
os.chdir(SRV)

from core import pc_agent as pa  # noqa: E402


def main():
    print("=== 工具清单 ===")
    print("IMPLS:", sorted(pa.TOOL_IMPLS.keys()))
    print("SCHEMA:", sorted(t["function"]["name"] for t in pa.TOOLS_SCHEMA))
    print()

    print("=== search_web（免 key 必应）===")
    print(pa.tool_search_web("ESP32-S3 是什么", max_results=3)[:600])
    print()

    print("=== search_files（桌面下 *.txt）===")
    print(pa.tool_search_files("桌面", "*.txt", max_results=10)[:800])
    print()

    # 造一个临时目录做归类测试
    d = Path(tempfile.mkdtemp(prefix="xz_org_"))
    for n in ("a.txt", "b.pdf", "c.jpg", "d.mp3", "e.mp4", "f.zip", "g.py", "h.xyz"):
        (d / n).write_text("x", encoding="utf-8")
    (d / "子目录").mkdir()

    print("=== organize_dir（默认 apply=False，只出计划）===")
    print(pa.tool_organize_dir(str(d)))
    print()
    print("  计划模式后，目录里文件数（应仍为 8 个文件 + 1 个子目录）：")
    print("   ", sorted(x.name for x in d.iterdir()))
    print()

    print("=== organize_dir（apply=True，真搬）===")
    print(pa.tool_organize_dir(str(d), apply=True))
    print()
    print("  执行后目录结构：")
    for x in sorted(d.rglob("*")):
        print("   ", x.relative_to(d), "<DIR>" if x.is_dir() else "")

    # 清理
    import shutil
    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
