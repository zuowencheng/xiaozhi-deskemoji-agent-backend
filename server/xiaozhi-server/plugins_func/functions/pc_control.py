# -*- coding: utf-8 -*-
"""
pc_control · 让小智真的能指挥本机电脑干活（投递箱写入端）
=========================================================

背景
----
小智说"帮我把桌面那个表格整理一下"这种话，**模型本身干不了**——它没有手。
真正的干法：把任务写进 `bridge/inbox/`，由本机的 WorkBuddy（助理）去执行，
执行完把结果写回 `bridge/done/`，再由常驻 watcher 主动播报回来。

本模块就是"写入端"：把 LLM 的意图变成一张投递箱任务单。

与谁对齐
--------
文件格式必须与 `bridge/server_agent.py` 的 `_task_body()` **逐字一致**，
否则助理侧解析不到（契约见 `docs/03-任务投递箱协议.md`）。

注册方式
--------
在 `data/.config.yaml` 的 `Intent.function_call.functions` 里加一行 `pc_control`
（注意：functions 是**列表**，覆盖式合并，必须把原有的也列全）。
模块名会被 `module_func_map` 展开成 `pc_run_task` / `pc_query_task` 两个工具。
"""

import os
from datetime import datetime
from pathlib import Path

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

# 本文件: <project>/server/xiaozhi-server/plugins_func/functions/pc_control.py
# parents: [0]=functions [1]=plugins_func [2]=xiaozhi-server [3]=server [4]=<project>
_PROJECT_ROOT = Path(__file__).resolve().parents[4]

# bridge/server_agent.py 写回任务单时用的**成功**状态字面量。
# ⚠️ 是 "done"，不是 "completed"（实测 41 个任务单：34 done / 7 failed）。
# 写错会让重复任务兜底逻辑静默失效（永远放行），还不报错，最难发现。
_STATUS_DONE = "done"
# 失败也算"派过了"。理由：那 3 条"我们早就做完的事"（开通火山 TTS ×2、关开场提示音）
# 在 done 里状态全是 failed —— 但**正因如此才更不能重复派**。
# failed 只说明"助理没做成"，不代表"这事还没做"，它恰恰是最该拦的一类。
_STATUS_FAILED = "failed"


def _bridge_dir() -> Path:
    """投递箱目录。可用环境变量 XZ_BRIDGE_DIR 覆盖（便携/搬迁时用）。"""
    env = os.environ.get("XZ_BRIDGE_DIR")
    return Path(env) if env else (_PROJECT_ROOT / "bridge")


def _dirs():
    base = _bridge_dir()
    inbox = base / "inbox"
    done = base / "done"
    announced = base / "announced"
    for d in (inbox, done, announced):
        d.mkdir(parents=True, exist_ok=True)
    return inbox, done, announced


def _new_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _task_body(tid: str, task: str, status: str = "pending", result: str = "",
               workspace: str = "") -> str:
    """与 bridge/server_agent.py::_task_body 保持逐字一致（别改格式）。

    2026-10-03 新增：可选「工作空间」行。主人说「在某某项目里帮我…」时，
    由 LLM 填 workspace，执行器会把 cwd 切到那个目录——
    这样小智派的活能落进主人指定的空间，而不是永远在一个地方。
    """
    ws_line = f"- 工作空间：{workspace}\n" if workspace else ""
    return (
        f"# 任务 {tid}\n\n"
        f"- 任务编号：{tid}\n"
        f"- 创建时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- 来源：语音（小智 deskemoji）\n"
        f"- 状态：{status}\n"
        f"{ws_line}\n"
        f"## 任务内容\n\n{task}\n\n"
        f"## 执行结果\n\n{result}\n"
    )


def _read_task(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    status = "unknown"
    for line in text.splitlines():
        if line.startswith("- 状态："):
            status = line.split("：", 1)[1].strip()
            break
    content = ""
    if "## 任务内容" in text:
        content = text.split("## 任务内容", 1)[1]
        if "## 执行结果" in content:
            content = content.split("## 执行结果", 1)[0]
        content = content.strip()
    result = ""
    if "## 执行结果" in text:
        result = text.split("## 执行结果", 1)[1].strip()
    return {"status": status, "content": content, "result": result}


# ---------------------------------------------------------------- 工具 1：派活
pc_run_task_desc = {
    "type": "function",
    "function": {
        "name": "pc_run_task",
        "description": (
            "把一项任务交给主人自己的电脑（本机 AI 助理）去执行。"
            "凡是需要「在电脑上动手」的事都必须调用本工具，"
            "例如：打开或关闭某个软件、整理/查找/改名文件、写文档、做表格、"
            "下载资料、跑脚本、改代码、上网查资料并汇总、分析某个文件内容等。"
            "调用后任务会进入待办队列，助理干完会主动播报结果。"
            "重要：调用本工具时回复要口语化地告诉主人「这就去办」，"
            "绝对不要说任务已经完成——真正的结果要等助理回执。"
            "\n"
            "⚠️ **派单前三道闸（都是实测踩出来的）**：\n"
            "1）**先 recall 查记忆**。如果主人说的事以前干过、或者本来就是你这台"
            "设备/后台能直接做的（例如改音色、换提示音、调音量、清理过程文档、"
            "开关某个开关），**根本不该派单**——那是本机配置层面的事，不是电脑任务。"
            "查到已完成就一句话告诉主人「这个早就弄好了」，别再派。\n"
            "2）**手机上的事不派**。手机 App 操作（比如用 workbody 打开美团外卖）"
            "本机助理做不到，派过去必然失败，直接跟主人说清做不到就行。\n"
            "3）**硬件故障不派**。显示器没信号、打印机坏了、网线松了——"
            "这类要人拿手去解决，派给助理只会浪费一轮。\n"
            "只有**确实需要在这台电脑上动手、且助理真能做**的活，才调用本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "要交给电脑执行的任务，用一句完整、具体、可独立理解的话描述。"
                        "例如「把桌面上所有 .jpg 图片按拍摄日期整理到子文件夹」"
                        "「帮我查一下今天 A 股上证指数的收盘点位并写成一个 txt 放到桌面」。"
                        "不要写成「帮我做一下刚才说的那个」这种依赖上下文的话。"
                        "⚠️ 一次只派一件明确的事。如果主人一句话里塞了多个目标"
                        "（比如「建任务再开个QQ再查油价」），**拆成多张单子**分别派，"
                        "别写在一张单子里——助理有步数上限，多目标任务会整单失败。"
                    ),
                },
                "workspace": {
                    "type": "string",
                    "description": (
                        "可选。主人指定的空间/项目名称或目录，例如「小智桌面助理」「2026-10-02-01-36-40」。"
                        "只有当主人明确说了「在……项目里」「在……空间里」时才填；"
                        "没说就留空，交给默认空间处理。"
                    ),
                },
            },
            "required": ["task"],
        },
    },
}


def _keywords(text: str):
    """从任务描述里抽出可比对的关键词集合。

    做法：清洗后切 2 字片段 + 抽 3~4 字"要点词"。
    为什么要 2 字片段：中文任务描述的关键词（"火山""音色""提示音"）多为双字，
    单字噪声太大。
    为什么要 3~4 字要点词：单靠 2 字片段会误判
    （实测"清理桌面过程文档目录" vs "在桌面「小智助手过程文档」目录上建立定期清理机制"
      2 字片段重合 78%，但明显是两件事）。
    加了要点词维度后，真正重复的任务重合度会更高，且"清理/建立机制"这种差异能被拉开。
    """
    clean = "".join(ch for ch in (text or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    grams = {clean[i:i + 2] for i in range(len(clean) - 1)}
    # 3~4 字要点词（去掉"帮我/一下"这类）
    for n in (3, 4):
        for i in range(len(clean) - n + 1):
            g = clean[i:i + n]
            if g in _STOPWORDS:
                continue
            grams.add(g)
    return grams


# 停用词：单独出现没有区分度，参与比对只会虚高重合度
_STOPWORDS = {
    "帮我", "帮忙", "一下", "这个", "那个", "现在", "马上", "立刻", "我要",
    "我想", "麻烦", "谢谢", "给我", "把", "的了", "啊吧", "吧呢", "吗",
    "然后", "并且", "还有", "另外", "可以", "进行", "相关", "已经", "之后",
    "再建", "建立", "机制", "看看", "试试", "确认", "检查", "汇报", "结果",
}

# 重复判定阈值。0.8 是实测定的：0.75 会误判"清理桌面过程文档" ≈ "建立清理机制"（见下方注释）
_DUP_RATIO = 0.8


def _recent_history(limit: int = 60):
    """扫最近若干条历史任务单（**done + failed 都算**）。

    ⚠️ 状态字面量是 `done` / `failed`，不是 `completed`（实测 41 单：34 done / 7 failed）。
    写错成 completed 会一条都匹配不上 → 兜底静默失效还不报错，最难发现。
    """
    try:
        _inbox, done, _ = _dirs()
    except Exception:
        return []
    out = []
    for p in sorted(done.glob("task-*.md"), reverse=True)[:limit]:
        d = _read_task(p)
        if d["status"] in (_STATUS_DONE, _STATUS_FAILED):
            out.append(d)
    return out


@register_function("pc_run_task", pc_run_task_desc, ToolType.WAIT)
def pc_run_task(task: str, workspace: str = ""):
    """
    把任务派给本机电脑上的 AI 助理（写进投递箱），返回任务编号。
    """
    task = (task or "").strip()
    if not task:
        return ActionResponse(Action.REQLLM, "任务内容为空，请告诉我要在电脑上做什么。", None)

    try:
        inbox, _, _ = _dirs()
    except Exception as e:
        logger.bind(tag=TAG).error(f"投递箱不可用: {e}")
        return ActionResponse(Action.REQLLM, f"投递箱打不开（{e}），没法把任务交出去。", None)

    # ⚠️ **failed 的任务单也要纳入比对**（实测踩出来的）：
    #   上面那三条在 done 里状态全是 `failed`（助理没做成），不是 `done`。
    #   只查 done 的话它们一条都命中不了 → 兜底等于没写（实测 4 个重复活只拦下 1 个）。
    #   而且 failed 恰恰是最该拦的：它说明"这事派过了、没做成"，再派大概率同样结果。
    #   而"真没做过的事"本来就不在 done 里，天然不会误拦。
    #
    # 阈值为什么提到 0.8：实测 0.75 会把「清理桌面过程文档目录」误判成
    #   「在桌面「小智助手过程文档」目录上建立定期清理机制」——
    #   2 字片段重合 78% 但明显是两件事。误挡 = 主人的新活儿被丢掉，
    #   代价远大于多派一次，所以宁可漏挡。0.8 + 要点词维度能把这类拉开。
    # ─────────────────────────────────────────────────────────────
    try:
        kw = _keywords(task)
        if len(kw) >= 4:   # 描述太短没有可比性，放行
            for d in _recent_history():
                dk = _keywords(d["content"])
                if not dk:
                    continue
                ratio = len(kw & dk) / min(len(kw), len(dk))
                if ratio >= _DUP_RATIO:
                    r = d["result"].replace("\n", " ").strip()
                    if len(r) > 120:
                        r = r[:120] + "…"
                    tag = ("已经办过了" if d["status"] == _STATUS_DONE
                           else "之前派过一次、没做成")
                    logger.bind(tag=TAG).info(
                        f"拦截重复任务（重合度 {ratio:.0%}, 旧状态={d['status']}）: "
                        f"{task[:40]} ≈ 已有「{d['content'][:40]}」"
                    )
                    return ActionResponse(
                        Action.REQLLM,
                        (
                            f"⚠️ 这件事{tag}，**别重复派**。\n"
                            f"历史任务单：「{d['content'][:60]}」（状态 {d['status']}）\n"
                            f"当时的结果：{r or '（空）'}\n\n"
                            f"严格要求：先用**一句自然口语**告诉主人这件事的情况，"
                            f"**不要派新任务**。二十五字以内。"
                        ),
                        None,
                    )
    except Exception as e:
        # 兜底逻辑本身出错绝不能影响正常派单
        # 兜底逻辑本身出错绝不能影响正常派单
        logger.bind(tag=TAG).warning(f"重复任务检查异常，放行: {e}")

    tid = _new_id()
    f = inbox / f"task-{tid}.md"
    # 同一秒内撞号：加后缀，绝不覆盖已有任务
    seq = 1
    while f.exists():
        f = inbox / f"task-{tid}-{seq}.md"
        seq += 1
        if seq > 50:
            return ActionResponse(Action.REQLLM, "任务编号生成失败，请稍后再试。", None)

    try:
        f.write_text(_task_body(tid, task, workspace=workspace), encoding="utf-8")
    except Exception as e:
        logger.bind(tag=TAG).error(f"写任务失败: {e}")
        return ActionResponse(Action.REQLLM, f"任务没能落盘（{e}），请稍后再试。", None)

    logger.bind(tag=TAG).info(f"任务已派发 -> {f.name}: {task[:60]}")
    # 2026-10-03 主人反馈：小智念"编号 20261003-010512"这一长串数字，很难听。
    # 修法：返回给 LLM 的指令里**不再带编号**，并明令禁止复述编号/原文。
    # 编号只落盘到任务单里（查询时用），不再进对话。
    return ActionResponse(
        Action.REQLLM,
        (
            f"任务已成功交给电脑上的助理，任务内容是「{task}」。"
            f"现在请用**一句自然口语**告诉主人这就去办，例如「好的，这就去办」「交给我吧」。"
            f"严格要求："
            f"1）绝对不要念任何编号、数字串、时间戳；"
            f"2）不要原样复述任务内容，用自己的话概括成一句话；"
            f"3）不要说任务已经完成——真正的结果要等助理回执后主动播报；"
            f"4）一句话，二十个字以内，别啰嗦。"
        ),
        None,
    )


# ---------------------------------------------------------------- 工具 2：问进度
pc_query_task_desc = {
    "type": "function",
    "function": {
        "name": "pc_query_task",
        "description": (
            "查询之前交给电脑的任务办到哪一步了，或者最近都办完了哪些事。"
            "当主人问「我让你干的那事怎么样了」「刚才那事办好了吗」"
            "「最近有哪些任务完成了」时调用。"
            "不传编号就返回最近的任务列表。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": (
                        "任务编号，形如 20261002-033310。"
                        "可选；不传则返回最近若干条任务的概览。"
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "不传编号时，返回最近多少条任务，默认 5，最多 15。",
                },
            },
            "required": [],
        },
    },
}


@register_function("pc_query_task", pc_query_task_desc, ToolType.WAIT)
def pc_query_task(task_id: str = "", limit: int = 5):
    """查询投递箱任务状态/结果。"""
    try:
        inbox, done, _ = _dirs()
    except Exception as e:
        return ActionResponse(Action.REQLLM, f"投递箱打不开（{e}）。", None)

    # --- 指定编号 ---
    if task_id:
        task_id = task_id.strip().lstrip("#")
        for base, where in ((done, "已完成"), (inbox, "待办中")):
            p = base / f"task-{task_id}.md"
            if p.exists():
                d = _read_task(p)
                if where == "已完成" and d["result"]:
                    r = d["result"].replace("\n", " ").strip()
                    if len(r) > 300:
                        r = r[:300] + "…"
                    return ActionResponse(
                        Action.REQLLM,
                        f"任务 {task_id} 已完成，结果是：{r}。请用口语化的话转述给主人。",
                        None,
                    )
                return ActionResponse(
                    Action.REQLLM,
                    f"任务 {task_id} 还在{where}，内容：「{d['content'][:80]}」。"
                    f"请告诉主人别急，办完会主动汇报。",
                    None,
                )
        return ActionResponse(
            Action.REQLLM, f"没找到编号为 {task_id} 的任务，请确认一下编号。", None
        )

    # --- 概览 ---
    try:
        limit = max(1, min(int(limit or 5), 15))
    except Exception:
        limit = 5

    done_items = sorted(done.glob("task-*.md"), reverse=True)[:limit]
    inbox_items = sorted(inbox.glob("task-*.md"), reverse=True)[:limit]

    lines = []
    for p in done_items:
        d = _read_task(p)
        r = d["result"].replace("\n", " ").strip() or "（空结果）"
        if len(r) > 60:
            r = r[:60] + "…"
        lines.append(f"【已完成】{d['content'][:30]} → {r}")
    for p in inbox_items:
        d = _read_task(p)
        lines.append(f"【待办中】{d['content'][:40]}")

    if not lines:
        return ActionResponse(
            Action.REQLLM, "投递箱里现在没有任务。请用一句口语化的话告诉主人。", None
        )

    return ActionResponse(
        Action.REQLLM,
        "最近的任务情况如下：" + "；".join(lines[:limit + 5]) + "。请用口语化的话向主人汇报。",
        None,
    )


# ══════════════════════════════════════════════════════════════
# ★ 2026-10-03 主人要求「桌面上建『小智助手过程文档』目录，
#   小智交互时产生的临时资料都扔这里，之后定期清理或者让它清理」。
#   这里给 LLM 一个显式工具，别指望它自己知道去翻目录。
# ══════════════════════════════════════════════════════════════
pc_clean_workdir_desc = {
    "type": "function",
    "function": {
        "name": "pc_clean_workdir",
        "description": (
            "清理桌面「小智助手过程文档」目录里的临时资料"
            "（截图、搜索缓存、任务中间产物等）。"
            "当主人说「清理过程文档 / 清一下桌面那些文件 / 小智的临时文件清掉」时使用。"
            "只清这个专用目录，不会碰主人其他任何文件。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "true=真删除；false或省略=只报数量让主人确认",
                }
            },
        },
    },
}


@register_function("pc_clean_workdir", pc_clean_workdir_desc, ToolType.WAIT)
def pc_clean_workdir(confirm: bool = False):
    from core.workdir import clean, stats
    try:
        st = stats()
        if not confirm:
            dry = clean(dry_run=True)
            if dry["count"] == 0:
                return ActionResponse(
                    Action.REQLLM,
                    "过程文档目录是空的，没有需要清理的东西。",
                    None,
                )
            return ActionResponse(
                Action.REQLLM,
                (f"过程文档目录里现在有 {dry['count']} 个文件，"
                 f"合计 {st['total_bytes']/1024/1024:.1f} MB。"
                 f"要我现在清掉吗？主人确认后再清。"),
                None,
            )
        r = clean()
        logger.bind(tag=TAG).info(f"已清理过程文档: {r}")
        return ActionResponse(
            Action.REQLLM,
            (f"清完了，删掉 {r['count']} 个文件，释放 {r.get('freed_mb', 0)} MB。"
             f"用一句话告诉主人清理结果，二十个字以内。"),
            None,
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"清理过程文档失败: {e}")
        return ActionResponse(Action.REQLLM, f"清理失败了（{e}），稍后再试。", None)
