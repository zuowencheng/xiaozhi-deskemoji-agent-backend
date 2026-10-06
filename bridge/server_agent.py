#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server_agent.py —— deskemoji 本机能力 MCP Server（投递箱版）

把本机 Windows 的能力暴露成 MCP 工具，供小智云端大模型调用。

⚠️ 铁律一：每个工具的 docstring 必须是普通字符串，绝不能写成 f-string，
   否则 FastMCP 发出的 description 是空串，模型会乱选工具。
⚠️ 铁律二：工具返回的字符串是"念给主人听的话"，要口语化、别堆 JSON。

任务投递箱协议（与 WorkBuddy 的契约，改目录结构前先看这里）：
    inbox/task-<id>.md       桥写入，status: pending
    done/task-<id>.md        WorkBuddy 执行完搬到这里，并填写「执行结果」段落
    announced/<id>.mark      已播报标记，避免重复念

工具清单：
    get_pc_status        本机状态（CPU/内存/磁盘/电池/开机时长）
    list_running_apps    占用内存最高的进程
    open_app             启动白名单应用
    read_text_file       读取文本文件内容
    dispatch_agent       投递任务给本机助理（写 inbox）
    query_task           按编号查任务结果
    list_recent_tasks    最近任务列表
    get_pending_results  取回"刚干完还没汇报"的任务（一次性，取走即标记已播报）
"""
import json
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import psutil
from mcp.server.fastmcp import FastMCP

import memory

# 后台预热嵌入模型，避免主人第一次问"你还记得吗"时卡十几秒
threading.Thread(target=memory.warmup, daemon=True).start()

BASE = Path(__file__).resolve().parent
INBOX = BASE / "inbox"
DONE = BASE / "done"
ANNOUNCED = BASE / "announced"
for d in (INBOX, DONE, ANNOUNCED):
    d.mkdir(exist_ok=True)

mcp = FastMCP("deskemoji-agent")

# 应用白名单（后续按需扩充）
APP_WHITELIST = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "explorer": "explorer.exe",
    "cmd": "cmd.exe",
    "chrome": r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "edge": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
}


# ---------------------------------------------------------------- 投递箱
def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _task_body(tid: str, task: str, status: str = "pending", result: str = "") -> str:
    return (
        f"# 任务 {tid}\n\n"
        f"- 任务编号：{tid}\n"
        f"- 创建时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- 来源：语音（小智 deskemoji）\n"
        f"- 状态：{status}\n\n"
        f"## 任务内容\n\n{task}\n\n"
        f"## 执行结果\n\n{result}\n"
    )


def _scan_done() -> list[dict]:
    """扫描 done/ 里所有已完成任务，附带是否已播报"""
    items = []
    for f in sorted(DONE.glob("task-*.md")):
        tid = f.name[len("task-"):-3]
        text = f.read_text(encoding="utf-8", errors="replace")
        result = ""
        if "## 执行结果" in text:
            result = text.split("## 执行结果", 1)[1].strip()
        title = ""
        if "## 任务内容" in text:
            title = text.split("## 任务内容", 1)[1].split("## 执行结果", 1)[0].strip()
        items.append({
            "id": tid,
            "title": title[:60],
            "result": result,
            "announced": (ANNOUNCED / f"{tid}.mark").exists(),
        })
    return items


def _take_pending(limit: int = 3, max_chars: int = 220) -> list[str]:
    """取出未播报的完成结果（取走即标记，避免重复念）"""
    out = []
    for it in _scan_done():
        if it["announced"] or not it["result"]:
            continue
        (ANNOUNCED / f"{it['id']}.mark").write_text(
            datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        r = it["result"].replace("\n", " ").strip()
        if len(r) > max_chars:
            r = r[:max_chars] + "…（完整结果已存到电脑上）"
        out.append(f"任务「{it['title']}」已完成：{r}")
        if len(out) >= limit:
            break
    return out


def _tail() -> str:
    """任何工具返回时都挂一句"顺便提醒"，这是云方案下最接近主动播报的做法"""
    pend = _take_pending()
    if not pend:
        return ""
    return "\n\n【顺便汇报】" + "；".join(pend)


# ---------------------------------------------------------------- 工具
@mcp.tool()
def get_pc_status() -> str:
    """查询本机电脑状态：CPU 占用、内存占用、C 盘剩余空间、电池电量、已开机时长。用于回答"电脑卡不卡""还有多少电""开了多久"这类问题。"""
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("C:\\")
    batt = psutil.sensors_battery()
    up = str(datetime.now() - datetime.fromtimestamp(psutil.boot_time())).split(".")[0]
    out = [
        f"CPU {cpu}%",
        f"内存 {mem.percent}%（已用 {mem.used // 2**30}GB / 共 {mem.total // 2**30}GB）",
        f"C 盘剩余 {disk.free // 2**30}GB / 共 {disk.total // 2**30}GB",
        f"已开机 {up}",
    ]
    if batt:
        out.append(f"电池 {batt.percent:.0f}%{'' if batt.power_plugged else '（未接电源）'}")
    return "；".join(out) + _tail()


@mcp.tool()
def list_running_apps(limit: int = 12) -> str:
    """列出当前占用内存最高的若干个进程，用于判断电脑在忙什么、有没有卡死的程序。limit 控制返回条数，默认 12。"""
    rows = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            rows.append((p.info["name"], p.info["memory_info"].rss))
        except Exception:
            continue
    rows.sort(key=lambda x: -x[1])
    return "；".join(f"{n}({m // 2**20}MB)" for n, m in rows[:limit]) + _tail()


@mcp.tool()
def open_app(name: str) -> str:
    """按代号启动本机应用，只允许白名单内的应用。可用代号：notepad（记事本）、calculator（计算器）、explorer（文件资源管理器）、cmd（命令行）、chrome、edge。传入英文代号。"""
    exe = APP_WHITELIST.get(name.lower().strip())
    if not exe:
        return f"不支持的应用：{name}。可用代号：{'、'.join(APP_WHITELIST)}" + _tail()
    try:
        subprocess.Popen(exe, shell=False)
        return f"已启动 {name}" + _tail()
    except Exception as e:
        return f"启动失败：{e!r}" + _tail()


@mcp.tool()
def read_text_file(path: str, max_chars: int = 1200) -> str:
    """读取本机一个文本文件的内容，默认最多返回 1200 字。用于让小智念出文件内容或查资料。只支持文本，不支持 Word/PDF/图片等二进制文件。"""
    p = Path(path)
    if not p.is_file():
        return f"文件不存在或不是文件：{path}" + _tail()
    try:
        t = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"读取失败：{e!r}" + _tail()
    if len(t) > max_chars:
        t = t[:max_chars] + "\n...（已截断，完整内容请到电脑上查看）"
    return t + _tail()


@mcp.tool()
def dispatch_agent(task: str) -> str:
    """把一项任务交给本机 AI 助理（WorkBuddy）去处理，返回任务编号。适合查资料、写文档、分析文件、改代码这类耗时的活。任务会写进 inbox 投递箱，助理处理后结果自动回传，下次对话会主动汇报。"""
    tid = _new_id()
    f = INBOX / f"task-{tid}.md"
    f.write_text(_task_body(tid, task), encoding="utf-8")
    try:
        memory.add("task", f"交给助理的任务（编号 {tid}）：{task}", "任务")
    except Exception:
        pass
    return (f"任务已交给助理，编号 {tid}。内容：{task[:60]}。"
            f"助理处理完会主动告诉你结果，你也可以随时问我要这个编号的结果。") + _tail()


@mcp.tool()
def remember(content: str, kind: str = "chat", tags: str = "") -> str:
    """把一条信息记进长期记忆，以后主人问起能想起来。主人告诉你任何偏好、习惯、事实、计划、项目进度、或刚刚说过的重要事情时，都要主动调用本工具记下来，不要等主人提醒。kind 用这五种之一：chat（说过的话）、profile（主人的习惯偏好）、project（在做什么项目及进度）、knowledge（知识要点）、task（干过什么活）。tags 是可选的关键词，用空格分隔。"""
    mid = memory.add(kind, content, tags)
    return f"记住了（第 {mid} 条，类别 {kind}）：{content[:40]}" + _tail()


@mcp.tool()
def recall(query: str, limit: int = 3) -> str:
    """从长期记忆里检索相关的旧事，支持语义模糊查找（问"我上次说的那个项目""我有什么习惯"都能找到）。主人问你记不记得什么、问过去说过的事、或者要引用之前的信息时，先调用本工具查记忆再回答。"""
    hits = memory.search(query, limit=limit)
    if not hits:
        return f"记忆里没有跟「{query}」相关的内容。" + _tail()
    return "我记得这些：" + "；".join(
        f"({h['kind']}) {h['content'][:90]}" for h in hits) + _tail()


@mcp.tool()
def list_memories(kind: str = "", limit: int = 8) -> str:
    """列出最近记住的内容，用于回答"你都记住了什么"。kind 留空表示全部，也可填 chat / profile / project / knowledge / task 只看某一类。"""
    rows = memory.recent(limit=limit, kind=kind or None)
    if not rows:
        return "记忆库还是空的。" + _tail()
    return "；".join(f"#{r['id']}({r['kind']}) {r['content'][:50]}" for r in rows) + _tail()


@mcp.tool()
def forget(memory_id: int) -> str:
    """按编号删除一条记忆。编号可以从 list_memories 里查到。主人明确说"忘掉某件事"时才用。"""
    ok = memory.delete(memory_id)
    return (f"已删除第 {memory_id} 条记忆。" if ok else f"没找到第 {memory_id} 条记忆。") + _tail()


@mcp.tool()
def query_task(task_id: str) -> str:
    """按任务编号查询任务的处理状态和结果。任务编号形如 20261002-031500。"""
    done_f = DONE / f"task-{task_id}.md"
    if done_f.exists():
        d = done_f.read_text(encoding="utf-8", errors="replace")
        result = d.split("## 执行结果", 1)[1].strip() if "## 执行结果" in d else ""
        return f"任务 {task_id} 已完成。{result}" + _tail()
    inbox_f = INBOX / f"task-{task_id}.md"
    if inbox_f.exists():
        return f"任务 {task_id} 还在排队等助理处理，稍等一下。" + _tail()
    return f"没有找到任务 {task_id}，可用 list_recent_tasks 查看最近任务" + _tail()


@mcp.tool()
def list_recent_tasks(limit: int = 5) -> str:
    """列出最近的若干任务及其状态，用于回答"我之前让你干的活怎么样了"。"""
    rows = []
    for f in sorted(DONE.glob("task-*.md"), reverse=True)[:limit]:
        tid = f.name[len("task-"):-3]
        d = f.read_text(encoding="utf-8", errors="replace")
        title = d.split("## 任务内容", 1)[1].split("## 执行结果", 1)[0].strip()[:40] if "## 任务内容" in d else ""
        rows.append(f"{tid} [已完成] {title}")
    for f in sorted(INBOX.glob("task-*.md"), reverse=True)[:limit]:
        tid = f.name[len("task-"):-3]
        d = f.read_text(encoding="utf-8", errors="replace")
        title = d.split("## 任务内容", 1)[1].split("## 执行结果", 1)[0].strip()[:40] if "## 任务内容" in d else ""
        rows.append(f"{tid} [处理中] {title}")
    return ("；".join(rows) if rows else "目前还没有登记过任务") + _tail()


@mcp.tool()
def get_pending_results() -> str:
    """取回刚刚干完、还没向主人汇报过的任务结果。说完一次就标记已汇报，不会重复念。用于主人问"刚才那事怎么样了"。"""
    pend = _take_pending(limit=5, max_chars=300)
    if not pend:
        return "暂时没有新完成的任务。"
    return "；".join(pend)


if __name__ == "__main__":
    mcp.run()
