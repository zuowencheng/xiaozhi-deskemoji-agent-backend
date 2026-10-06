# -*- coding: utf-8 -*-
"""
task_board · 小智任务中心看板（给主人肉眼看进度用）
==================================================

干什么
------
把 `bridge/inbox` + `bridge/done` 里的任务单，渲染成一份**人能读的 Markdown 看板**，
写到项目根的 `小智任务中心.md`。主人打开 WorkBuddy 就能看到：

    🟡 进行中 / ⏳ 待办 / ✅ 已完成（含结果摘要）/ ❌ 失败

为什么需要
----------
语音交互是"一锤子买卖"——小智说完就过去了，主人没法回头查。
但派出去的活是要**跟踪**的：到底干没干、干成啥样、卡在哪一步。
看板把这件事从"只能靠嘴问"变成"打开就能看见"。

顺带解决了"我的任务在 WorkBuddy 界面里看不到"这个诉求：
看板就是项目里的一份普通 md，WorkBuddy 界面直接能打开、能搜、能查状态。

谁调用
------
- `core/notify_plugin.py` 的执行循环：每轮扫完刷一次
- 任务状态变化（派发/开跑/完成/失败）时立即刷一次
- 也可以单独跑：`python core/task_board.py` （服务端目录下）

不改上游、不依赖网络，纯本地文件读写。
"""

import os
from datetime import datetime
from pathlib import Path

# core/task_board.py -> [0]=core [1]=xiaozhi-server [2]=server [3]=<项目根>
_SERVER_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BOARD_FILE = _PROJECT_ROOT / "小智任务中心.md"


def _bridge_dir() -> Path:
    env = os.environ.get("XZ_BRIDGE_DIR")
    return Path(env) if env else (_PROJECT_ROOT / "bridge")


def _parse(path: Path) -> dict:
    """解析一张任务单（格式见 bridge/server_agent.py::_task_body）"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    d = {
        "tid": path.name[len("task-"):-len(".md")] if path.name.startswith("task-") else path.stem,
        "title": "",
        "status": "unknown",
        "created": "",
        "source": "",
        "result": "",
        "mtime": datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M"),
    }
    for line in text.splitlines():
        if line.startswith("- 创建时间："):
            d["created"] = line.split("：", 1)[1].strip()
        elif line.startswith("- 来源："):
            d["source"] = line.split("：", 1)[1].strip()
        elif line.startswith("- 状态："):
            d["status"] = line.split("：", 1)[1].strip()
    if "## 任务内容" in text:
        body = text.split("## 任务内容", 1)[1]
        if "## 执行结果" in body:
            body = body.split("## 执行结果", 1)[0]
        d["title"] = body.strip().replace("\n", " ")
    if "## 执行结果" in text:
        d["result"] = text.split("## 执行结果", 1)[1].strip().replace("\n", " ")
    return d


_STATUS_ICON = {
    "pending": ("⏳", "待办"),
    "running": ("🟡", "进行中"),
    "done": ("✅", "已完成"),
    "failed": ("❌", "失败"),
}


def render(limit: int = 30) -> str:
    base = _bridge_dir()
    inbox, done = base / "inbox", base / "done"

    items = []
    for p in list(inbox.glob("task-*.md")) + list(done.glob("task-*.md")):
        d = _parse(p)
        if d:
            items.append(d)
    # 按创建时间倒序，最新的在上面
    items.sort(key=lambda x: (x.get("created") or "", x["tid"]), reverse=True)
    items = items[:limit]

    buckets = {"running": [], "pending": [], "done": [], "failed": [], "unknown": []}
    for it in items:
        buckets.setdefault(it["status"], []).append(it)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# 小智·任务中心",
        "",
        f"> 自动更新于 {now}　|　共 {len(items)} 条　|　"
        f"进行中 {len(buckets['running'])} · 待办 {len(buckets['pending'])} · "
        f"完成 {len(buckets['done'])} · 失败 {len(buckets['failed'])}",
        "",
        "这份文件由后台自动生成，**别手改**（改了也会被覆盖）。",
        "任务由语音派发（对桌上小智说一句话即可），由本机助理执行，结果会主动播报回设备。",
        "",
    ]

    for key in ("running", "pending", "failed", "done"):
        group = buckets.get(key) or []
        icon, name = _STATUS_ICON.get(key, ("•", key))
        lines.append(f"## {icon} {name}（{len(group)}）")
        lines.append("")
        if not group:
            lines.append("_（无）_")
            lines.append("")
            continue
        for it in group:
            title = it["title"] or "（无标题）"
            if len(title) > 80:
                title = title[:80] + "…"
            lines.append(f"- **{it['created'] or it['mtime']}** · `{it['tid']}`")
            lines.append(f"  - 任务：{title}")
            if it["source"]:
                lines.append(f"  - 来源：{it['source']}")
            if it["result"]:
                r = it["result"]
                if len(r) > 200:
                    r = r[:200] + "…"
                lines.append(f"  - 结果：{r}")
        lines.append("")

    lines += [
        "---",
        "",
        "### 怎么用",
        "",
        "1. **派活**：对桌上的小智说「帮我把……」，它会交给本机助理并回一句「这就去办」。",
        "2. **看进度**：打开本文件，或访问管理台 http://127.0.0.1:8005 。",
        "3. **听结果**：助理干完，小智会主动开口播报，不用你再问。",
        "4. **指定空间**：说「在某某项目里……」，任务会在对应目录执行。",
        "",
        "_后台每轮扫描自动重写本文件。_",
    ]
    return "\n".join(lines)


def refresh(limit: int = 30) -> str:
    """重写看板文件。返回文件路径。发生异常不抛（看板是锦上添花，不能拖垮主流程）。"""
    try:
        _BOARD_FILE.write_text(render(limit), encoding="utf-8")
        return str(_BOARD_FILE)
    except Exception:
        return ""


if __name__ == "__main__":
    print(refresh())
