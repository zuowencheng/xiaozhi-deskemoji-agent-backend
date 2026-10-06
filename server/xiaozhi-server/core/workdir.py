# -*- coding: utf-8 -*-
"""小智工作目录（2026-10-03 主人要求）

主人原话：「他交互时生成的临时资料，都扔到这个文档里边（桌面『小智助手过程文档』）。
到时候我会定期去清理，或者让他去清理。」

**为什么要这个模块**
小智干活时会在磁盘上留下东西：截图、联网搜到的资料、下载的文件、中间产物。
以前散落在桌面根目录 / 服务端 tmp / 随机路径 —— 桌面被堆满、找不到、也不敢删。
统一收进一个目录后才敢定期清理。

**目录结构**（首次调用自动创建）
    桌面/小智助手过程文档/
        截图/       抓屏（pc_desktop.screenshot 默认落这里）
        搜索资料/   联网搜索/抓网页的落盘结果
        任务产出/   小智干活的结果文件（文档、表格）
        临时文件/   中间产物，可随时删
        README.txt  给人看的说明

**设计要点**
1. 目录名/子目录名**不散落在代码各处** —— 统一走 `work_dir(kind)`
2. 每次调用都 mkdir（幂等），不存在不会报错
3. 提供 `clean()` 供小智被要求"清理过程文档"时调用
4. 提供 `stats()` 供管理台展示"这个目录占多大、什么时候清过"
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

# 目录名可用环境变量覆盖（测试或多实例场景）
ROOT_ENV = "XIAOZHI_WORKDIR"
DEFAULT_NAME = "小智助手过程文档"

#: 允许的子目录类型。传别的名字会 fallback 到「临时文件」，
#: 避免 LLM 随口编一个名字就在桌面根上建出奇怪文件夹。
KINDS = {
    "截图": ("screenshot", "screenshot"),
    "搜索资料": ("search", "search"),
    "任务产出": ("task_output", "output"),
    "临时文件": ("tmp", "temp"),
}
_FALLBACK = "临时文件"


def root() -> Path:
    """小智工作目录根（桌面/小智助手过程文档）。"""
    env = os.environ.get(ROOT_ENV)
    if env:
        base = Path(env)
    else:
        base = Path(os.path.expanduser("~")) / "Desktop" / DEFAULT_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def work_dir(kind: str = _FALLBACK) -> Path:
    """按用途取子目录，自动创建。传错名字落到「临时文件」。"""
    name = kind if kind in KINDS else _FALLBACK
    d = root() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def rel(p) -> str:
    """把绝对路径转成「相对于工作目录」的短路径，播报时更好念。"""
    try:
        return str(Path(p).relative_to(root()))
    except Exception:
        return str(p)


def stats() -> dict:
    """目录体检：给管理台 / 回答"过程文档多大"用。"""
    r = root()
    out = {"root": str(r), "exists": r.exists(), "kinds": {}, "total_files": 0,
           "total_bytes": 0, "cleanup_count": 0, "last_cleanup": ""}
    for name in KINDS:
        d = r / name
        files = list(d.glob("*")) if d.exists() else []
        nb = 0
        for f in files:
            try:
                if f.is_file():
                    nb += f.stat().st_size
            except Exception:
                pass
        out["kinds"][name] = {"files": len(files), "bytes": nb}
        out["total_files"] += len(files)
        out["total_bytes"] += nb
    for f in sorted(r.glob("清理记录-*.txt")):
        out["cleanup_count"] += 1
        out["last_cleanup"] = out["last_cleanup"] or f.stem.replace("清理记录-", "")
    return out


def clean(dry_run: bool = False) -> dict:
    """清空工作目录（默认连子目录一起清，但保留目录本身和 README）。

    dry_run=True 时只报告要删什么，不真删 —— 「看着不像要动的东西先说一遍」。
    """
    r = root()
    targets = []
    for name in KINDS:
        d = r / name
        if d.exists():
            for f in d.iterdir():
                if f.is_file():
                    targets.append(f)
    if dry_run:
        return {"ok": True, "dry_run": True, "count": len(targets),
                "files": [rel(f) for f in targets[:40]]}
    freed = 0
    for f in targets:
        try:
            freed += f.stat().st_size
            f.unlink()
        except Exception:
            pass
    # 留一份清理记录，方便回看什么时候清过
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (r / f"清理记录-{stamp}.txt").write_text(
        f"清理时间：{datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"删除文件：{len(targets)} 个\n"
        f"释放空间：{freed/1024/1024:.2f} MB\n", encoding="utf-8")
    return {"ok": True, "count": len(targets), "freed_mb": round(freed / 1024 / 1024, 2)}


if __name__ == "__main__":
    import json
    print(json.dumps(stats(), ensure_ascii=False, indent=2))
    print("\n--- 试运行清理 ---")
    print(json.dumps(clean(dry_run=True), ensure_ascii=False, indent=2))
