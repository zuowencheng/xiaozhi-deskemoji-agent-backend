# -*- coding: utf-8 -*-
"""
memory_tools · 把本机记忆层接进语音
====================================

背景
----
`bridge/memory.py` 早就写好了（SQLite + 本地 Ollama 嵌入 + 关键词兜底，数据不出网），
但它只是一个命令行工具，语音侧一直没接上——小智"记得住"这件事一直是空的。

本模块把它包成 4 个 LLM 工具：
    remember        记一件事（五类：chat/profile/project/knowledge/task）
    recall          语义检索"我以前说过/你知道什么"
    list_memories   按类型列最近若干条
    forget          按 id 删除一条

设计要点
--------
- **绝不阻塞语音**：memory.py 里 Ollama 挂了会自动降级为纯关键词检索；这里再兜一层
  异常捕获，任何失败都返回一句"没查到"，不让整轮对话崩。
- **绕过本机代理**：本机跑着 10808/10809 代理，urllib 会把 127.0.0.1 也塞进代理导致 502。
  这里显式设 no_proxy，保证 Ollama 本地调用直连。
- 记忆库不进仓库（`bridge/memory/` 已在 .gitignore）。

注册方式
--------
在 `data/.config.yaml` 的 `Intent.function_call.functions` 里加一行 `memory_tools`
（functions 是**列表**、覆盖式合并，必须把原有项也列全）。
"""

import importlib.util
import os
from pathlib import Path

from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

# 本文件: <project>/server/xiaozhi-server/plugins_func/functions/memory_tools.py
# parents: [0]=functions [1]=plugins_func [2]=xiaozhi-server [3]=server [4]=<project>
_PROJECT_ROOT = Path(__file__).resolve().parents[4]

# 让 127.0.0.1 直连，别走本机代理（否则 Ollama 调用会拿到 502）
for _k in ("NO_PROXY", "no_proxy"):
    _v = os.environ.get(_k, "")
    if "127.0.0.1" not in _v:
        os.environ[_k] = (_v + ",127.0.0.1,localhost").strip(",")

_MEM = None
_MEM_ERR = ""


def _memory():
    """懒加载 bridge/memory.py；加载失败返回 None（不抛异常）。"""
    global _MEM, _MEM_ERR
    if _MEM is not None:
        return _MEM
    try:
        # 允许用环境变量指定记忆库位置（便携/搬迁）
        env = os.environ.get("XZ_BRIDGE_DIR")
        base = Path(env) if env else (_PROJECT_ROOT / "bridge")
        path = base / "memory.py"
        if not path.exists():
            _MEM_ERR = f"未找到记忆模块 {path}"
            return None
        spec = importlib.util.spec_from_file_location("xz_bridge_memory", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MEM = mod
        logger.bind(tag=TAG).info(f"记忆模块已加载: {path}")
        return _MEM
    except Exception as e:
        _MEM_ERR = str(e)
        logger.bind(tag=TAG).error(f"记忆模块加载失败: {e}")
        return None


def _short(s: str, n: int = 80) -> str:
    s = (s or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------- 记
remember_desc = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": (
            "把一件值得长期记住的事存进本地记忆库。当主人说「记住…」「以后…就叫…」"
            "「我的…是…」「我正在做…」这类需要长期留存的信息时调用。"
            "不要把每句闲聊都记下来——只记有长期价值的事实、偏好、项目进展或任务结果。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记住的内容，一句完整、可脱离上下文理解的话。",
                },
                "kind": {
                    "type": "string",
                    "enum": ["chat", "profile", "project", "knowledge", "task"],
                    "description": (
                        "记忆类型：profile=主人的习惯/偏好；project=在做的项目与进度；"
                        "knowledge=资料/台账要点；task=干过的活与结果；chat=普通对话。"
                        "默认 profile。"
                    ),
                },
                "tags": {
                    "type": "string",
                    "description": "可选，逗号分隔的标签，便于检索，例如「小智,硬件」。",
                },
            },
            "required": ["content"],
        },
    },
}


@register_function("remember", remember_desc, ToolType.WAIT)
def remember(content: str, kind: str = "profile", tags: str = ""):
    """写入一条记忆。"""
    content = (content or "").strip()
    if not content:
        return ActionResponse(Action.REQLLM, "要记的内容是空的，请告诉我要记住什么。", None)
    mem = _memory()
    if mem is None:
        return ActionResponse(Action.REQLLM, f"记忆库不可用（{_MEM_ERR}），这次没记下来。", None)
    try:
        mid = mem.add(kind, content, tags)
        logger.bind(tag=TAG).info(f"已记忆 #{mid} ({kind}): {_short(content)}")
        return ActionResponse(
            Action.REQLLM,
            f"已经记住了（编号 {mid}）。请用一句口语化的话告诉主人「记下了」。",
            None,
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"记忆写入失败: {e}")
        return ActionResponse(Action.REQLLM, f"记忆没存上（{e}），稍后再试。", None)


# ---------------------------------------------------------------- 查
recall_desc = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": (
            "从本地记忆库里检索相关信息。当主人问「我以前说过…吗」「你还记得…吗」"
            "「我对…的偏好是什么」「那个项目现在什么样」时调用。"
            "拿到的片段要自然地融入回答，不要照本宣科念编号。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词或一句话，例如「固件升级」「主人的音乐偏好」。",
                },
                "limit": {"type": "integer", "description": "返回条数，默认 5，最多 10。"},
                "kind": {
                    "type": "string",
                    "enum": ["chat", "profile", "project", "knowledge", "task"],
                    "description": "限定记忆类型；不传则全库检索。",
                },
            },
            "required": ["query"],
        },
    },
}


@register_function("recall", recall_desc, ToolType.WAIT)
def recall(query: str, limit: int = 5, kind: str = ""):
    """语义检索记忆。"""
    query = (query or "").strip()
    if not query:
        return ActionResponse(Action.REQLLM, "要查什么呢？请给我个关键词。", None)
    mem = _memory()
    if mem is None:
        return ActionResponse(Action.REQLLM, f"记忆库不可用（{_MEM_ERR}）。", None)
    try:
        limit = max(1, min(int(limit or 5), 10))
    except Exception:
        limit = 5
    try:
        rows = mem.search(query, limit=limit, kind=(kind or None))
    except Exception as e:
        logger.bind(tag=TAG).error(f"记忆检索失败: {e}")
        return ActionResponse(Action.REQLLM, f"记忆检索出错（{e}）。", None)

    # 相关度门槛：实测 bge-m3 下"真相关"普遍 ≥0.7（固件升级 0.718、说话风格 0.83），
    # 而"毫不相干"会被压到 0.32 左右。设 0.45 可挡住硬凑的结果，避免模型编故事。
    # 降级（Ollama 不可用、纯关键词）时关键词上限约 0.55，此门槛仍能放行较强命中。
    MIN_SCORE = 0.45
    rows = [r for r in rows if float(r.get("score", 0)) >= MIN_SCORE]

    if not rows:
        return ActionResponse(
            Action.REQLLM,
            f"记忆库里没有和「{query}」明显相关的记录。请如实告诉主人没查到，不要编造。",
            None,
        )
    lines = [
        f"({r.get('kind')}) {_short(r.get('content', ''), 90)}" for r in rows
    ]
    return ActionResponse(
        Action.REQLLM,
        "检索到的相关记忆如下：\n- " + "\n- ".join(lines) + "\n请结合这些内容自然地回答主人。",
        None,
    )


# ---------------------------------------------------------------- 列
list_memories_desc = {
    "type": "function",
    "function": {
        "name": "list_memories",
        "description": (
            "按时间倒序列出最近的记忆条目。当主人说「你都记得些什么」「列一下我的记忆」"
            "或者想清理记忆时调用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "返回条数，默认 10，最多 30。"},
                "kind": {
                    "type": "string",
                    "enum": ["chat", "profile", "project", "knowledge", "task"],
                    "description": "只看某一类；不传则所有类型混排。",
                },
            },
            "required": [],
        },
    },
}


@register_function("list_memories", list_memories_desc, ToolType.WAIT)
def list_memories(limit: int = 10, kind: str = ""):
    """列出最近记忆。"""
    mem = _memory()
    if mem is None:
        return ActionResponse(Action.REQLLM, f"记忆库不可用（{_MEM_ERR}）。", None)
    try:
        limit = max(1, min(int(limit or 10), 30))
    except Exception:
        limit = 10
    try:
        rows = mem.recent(limit=limit, kind=(kind or None))
    except Exception as e:
        return ActionResponse(Action.REQLLM, f"读取记忆失败（{e}）。", None)
    if not rows:
        return ActionResponse(
            Action.REQLLM, "记忆库现在是空的。请用一句口语化的话告诉主人。", None
        )
    lines = [f"#{r.get('id')} ({r.get('kind')}) {_short(r.get('content', ''), 70)}" for r in rows]
    return ActionResponse(
        Action.REQLLM,
        "最近的记忆如下（含编号，可用于删除）：\n- " + "\n- ".join(lines) + "\n请向主人简要概括。",
        None,
    )


# ---------------------------------------------------------------- 删
forget_desc = {
    "type": "function",
    "function": {
        "name": "forget",
        "description": (
            "根据编号删除一条记忆。主人说「忘掉 …」「把那条记忆删了」时调用。"
            "编号要先通过 list_memories 或 recall 拿到，不要凭空猜。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "integer", "description": "要删除的记忆编号（#后面的数字）。"}
            },
            "required": ["memory_id"],
        },
    },
}


@register_function("forget", forget_desc, ToolType.WAIT)
def forget(memory_id: int):
    """按 id 删除记忆。"""
    mem = _memory()
    if mem is None:
        return ActionResponse(Action.REQLLM, f"记忆库不可用（{_MEM_ERR}）。", None)
    try:
        mid = int(memory_id)
    except Exception:
        return ActionResponse(Action.REQLLM, "编号得是数字，请确认一下。", None)
    try:
        ok = mem.delete(mid)
    except Exception as e:
        return ActionResponse(Action.REQLLM, f"删除失败（{e}）。", None)
    if ok:
        return ActionResponse(Action.REQLLM, f"已忘掉编号 {mid} 那条。请告诉主人。", None)
    return ActionResponse(Action.REQLLM, f"没找到编号 {mid} 的记忆。", None)
