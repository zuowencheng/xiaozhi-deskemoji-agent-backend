# -*- coding: utf-8 -*-
"""大脑（大模型）自切换与自报家底 —— 2026-10-03

主人原话：「让他直接切，并让他告诉我现在用的是什么大脑，也就是用的后台模型。」

**为什么不让它自动生效就完事**
切换 LLM 有两处要改：
1. `data/.config.yaml` 的 `selected_module.LLM`（**持久化**，否则重启就回滚）
2. `conn.llm` 内存实例（**当前会话**改完立刻生效，不用重启服务）
只改 1 → 必须重启；只改 2 → 重启后失效。两处都改才既快又持久。

**为什么不做"额度烧穿自动切"**
静默换模型是危险的：主人不知道小智的脑子换了，等下一轮发现语气变了才察觉。
所以这里的策略是**先播报、确认后才切**，且切完主动报出新大脑名。
（`llm_usage.pick_llm()` 只给建议，不自动执行。）

**工具两个**
- `switch_llm(name)` —— 切换大脑（立刻生效 + 持久化）
- `list_llms()` —— 报家底：现在用哪个 + 还有哪些可切

主人原话：「让他直接切，并让他告诉我现在用的是什么大脑，也就是用的后台模型。」
所以这两个工具都注册成 LLM 可调用的 function，
小智被问「你现在用什么模型」时会自己去调 list_llms，
被说「把大脑切到 X」时会自己去调 switch_llm。
"""
from __future__ import annotations

import re
from pathlib import Path

from config.logger import setup_logging
from plugins_func.register import Action, ActionResponse, ToolType, register_function

TAG = __name__
logger = setup_logging()


# ─────────────────────── 配置读写（最小实现） ───────────────────────
# 为什么不用 config/settings：那个是全局单例，启动时 load 一次。
# 这里只改「LLM 选哪个」这一个字段，用行级正则精确替换，
# 避免整个 YAML 被重排（管理台保存失败过一次，就是因为重排破坏了缩进）。
_CFG = Path(__file__).resolve().parents[2] / "data" / ".config.yaml"


def _read_current() -> str:
    """读当前选中的 LLM 模块名。"""
    try:
        txt = _CFG.read_text(encoding="utf-8")
    except Exception:
        return ""
    # selected_module 段里的 LLM: xxx
    m = re.search(r"^selected_module:\s*$(.*?)(?=^\S|\Z)", txt, re.S | re.M)
    seg = m.group(1) if m else txt
    m2 = re.search(r"^\s+LLM:\s*(\S+)\s*$", seg, re.M)
    return m2.group(1) if m2 else ""


def _write_current(name: str) -> tuple:
    """把 selected_module.LLM 改成 name。返回 (成功, 说明)。"""
    try:
        txt = _CFG.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"配置文件读不到（{e}）"
    if not re.search(r"^(\s+)LLM:\s*\S+\s*$", txt, re.M):
        return False, "配置文件里找不到 selected_module.LLM 这一行"
    new = re.sub(r"^(\s+)(LLM:)\s*\S+\s*$", r"\1\2 " + name, txt, count=1, flags=re.M)
    if new == txt:
        return False, f"替换失败：{name} 写不进去"
    try:
        _CFG.write_text(new, encoding="utf-8")
    except Exception as e:
        return False, f"配置写失败（{e}）"
    return True, ""


# ══════════════════════════════════════════════════════════════
# ★★ 2026-10-03 修一个会导致「切完模型后台就起不来」的严重缺陷
# ══════════════════════════════════════════════════════════════
# 现象（真实踩到）：19:17 让小智把大脑切到通义千问，它回报"已切换（立即生效=True）"，
#   **19:19 重启后台后直接起不来**：
#     `KeyError: 'QwenLLM'` @ core/utils/modules_initialize.py:41
#   只能手工把 `selected_module.LLM` 改回 DeepSeekLLM 才救回来。
#
# 根因（读写不对称）：
#   `llm_profiles` 里的档案分两种写法 ——
#     A) 引用式：只有 label/note，真正配置在 `LLM:` 段同名下（如 DeepSeekLLM）
#     B) 完整式：自带 type/model_name/base_url/api_key（如 QwenLLM）
#   切换只改了 `selected_module.LLM` 这一个字段：
#     - 切到 A 类 → 启动时 `config["LLM"]["DeepSeekLLM"]` 找得到 → 正常
#     - 切到 B 类 → `config["LLM"]["QwenLLM"]` **不存在** → 启动直接 KeyError
#   也就是说：**能切、不能重启**，切换功能等于把后台搞坏了。
#
# 修法：切换成功后，若 `LLM:` 段没有同名条目，就把这个档案的运行时字段
#   （type/model_name/base_url/api_key/temperature/max_tokens/timeout）
#   补写进 `LLM:` 段。这样重启时 config["LLM"][name] 就找得到。
#   写的是**运行时必需的字段**，label/note/need_key 这类展示字段不写。
# ══════════════════════════════════════════════════════════════
_RUNTIME_KEYS = ("type", "model_name", "base_url", "url", "api_key",
                 "temperature", "max_tokens", "top_p", "frequency_penalty",
                 "timeout")


def _ensure_llm_section(name: str) -> tuple:
    """确保 `LLM:` 段里有 name 这个条目，缺就按档案补写。返回 (成功, 说明)。"""
    try:
        import yaml
    except Exception:
        return False, "没有 yaml 模块"
    try:
        cfg = yaml.safe_load(_CFG.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return False, f"读配置失败（{e}）"

    llm_sec = cfg.get("LLM")
    if isinstance(llm_sec, dict) and name in llm_sec:
        return True, "LLM 段已有同名配置"

    prof = (cfg.get("llm_profiles") or {}).get(name)
    if not isinstance(prof, dict):
        return False, f"llm_profiles 里没有 {name}"

    runtime = {k: v for k, v in prof.items() if k in _RUNTIME_KEYS and v is not None}
    if not runtime.get("type"):
        return False, f"{name} 是引用式档案但 LLM 段里没有它（配置不完整）"

    # 用 yaml 整体重写，而不是正则往文本里插 ——
    # 正则插入踩过坑：LLM: 段最后一项是 `DoubaoLLM:`（带尾冒号、无子键），
    # 插在它下面会拼成 `DoubaoLLM:  QwenLLM:` → YAML 直接崩。
    # 整体 dump 最稳，代价只是注释丢失 —— 但这是 data/ 下的私有配置，可接受。
    try:
        llm_sec = dict(llm_sec or {})
        llm_sec[name] = runtime
        cfg["LLM"] = llm_sec
        _CFG.write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
    except Exception as e:
        return False, f"写 LLM 段失败（{e}）"

    # 语法自检：写坏了绝不能留（否则下次重启直接崩）
    try:
        chk = yaml.safe_load(_CFG.read_text(encoding="utf-8")) or {}
        if name not in (chk.get("LLM") or {}):
            raise ValueError("写入后 LLM 段里找不到该条目")
    except Exception as e:
        _CFG.write_text(txt, encoding="utf-8")   # 回滚
        return False, f"写入后校验失败，已回滚（{e}）"

    return True, f"已补写 LLM.{name}"


def _profiles() -> dict:
    """读 llm_profiles。"""
    try:
        import yaml
        txt = _CFG.read_text(encoding="utf-8")
        cfg = yaml.safe_load(txt) or {}
    except Exception:
        return {}
    return cfg.get("llm_profiles") or {}


def _has_usable_key(seg: dict) -> bool:
    """档案能不能真用（key 不是占位符）。"""
    if not seg.get("need_key"):
        return True
    k = (seg.get("api_key") or "").strip()
    if not k:
        return False
    if k.endswith("-xxx") or "填你的" in k or k == "your-key":
        return False
    return True


# 档案里我们额外加的展示字段，**不是 LLMProvider 需要的参数**，要剔掉
_PROFILE_ONLY = ("label", "note", "note2", "need_key", "tokens_seen")


def _build_llm(name: str):
    """按档案建一个 LLM 实例（立刻用）。失败抛异常。

    ⚠️ 2026-10-03 修一个会导致「切回不来」的设计缺陷：
    档案有两种写法 ——
      A) **完整配置**（自带 type/model_name/base_url/api_key），如 QwenLLM
      B) **引用式**（只有 label/note，真正的配置在 `LLM:` 段同名下），如 DeepSeekLLM
    原来只按 A 处理，遇到 B 就抛「缺 type 字段」→ **主人切过去就切不回来了**。
    现在：先看 `LLM:` 段有没有同名段，有就用它（那才是运行时真正用的配置），
    没有再用档案本身。
    """
    from core.utils import llm as llm_utils
    import yaml
    cfg = yaml.safe_load(_CFG.read_text(encoding="utf-8")) or {}
    prof = (cfg.get("llm_profiles") or {}).get(name)
    if not prof:
        raise ValueError(f"没有叫 {name} 的档案")

    # ① 优先用 LLM 段的同名配置（引用式档案的真身在这里）
    real = (cfg.get("LLM") or {}).get(name)
    if isinstance(real, dict) and real.get("type"):
        return llm_utils.create_instance(real["type"], real)

    # ② 否则用档案自身（完整配置式）
    seg = {k: v for k, v in prof.items() if k not in _PROFILE_ONLY}
    if seg.get("type"):
        return llm_utils.create_instance(seg["type"], seg)
    raise ValueError(f"{name} 既没有完整配置，LLM 段里也找不到它")


def _friendly(name: str) -> str:
    """模块名 → 主人听得懂的名字。"""
    p = _profiles().get(name) or {}
    lab = p.get("label")
    if lab:
        # 去掉括号里的补充说明，只留主干
        out = re.split(r"[（(]", lab)[0].strip()
        # 再去掉型号尾巴（"通义千问 qwen-plus" → "通义千问"）
        out = re.sub(r"\s+(qwen|deepseek)[\w.\-]*$", "", out, flags=re.I).strip()
        return out or name
    pretty = {"DeepSeekLLM": "DeepSeek", "QwenLLM": "通义千问",
              "OllamaLLM": "本地 Ollama", "DoubaoLLM": "豆包",
              "OllamaOpenAI": "本地模型", "DeepSeekV4LLM": "DeepSeek V4"}
    return pretty.get(name, name)


# ══════════════════════════════════════════════════════════════
# 工具 1：报家底（现在用的哪个大脑 + 有哪些可切）
# ══════════════════════════════════════════════════════════════
list_llms_desc = {
    "type": "function",
    "function": {
        "name": "list_llms",
        "description": (
            "报出当前正在使用的大模型（大脑/后台模型）是什么，以及还有哪些大模型可以切换。"
            "当主人问「你现在用什么模型 / 你脑子是啥 / 有哪些大脑能换 / 切模型」时使用。"
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


@register_function("list_llms", list_llms_desc, ToolType.WAIT)
def list_llms():
    cur = _read_current()
    profs = _profiles()
    lines = [f"你现在用的是「{_friendly(cur)}」（模块 {cur}）。"]
    others = []
    for k, v in profs.items():
        if k == cur:
            continue
        usable = _has_usable_key(v)
        label = re.split(r"[（(]", v.get("label") or k)[0].strip()
        others.append(f"{label}（{k}）" + ("" if usable else "，但还没配 key，暂时不能用"))
    if others:
        lines.append("还可以切到：" + "、".join(others) + "。")
        lines.append("要切就跟主人说「把大脑切到 XXX」，我会确认后帮你切，并告诉你切换结果。")
    else:
        lines.append("暂时没有其他可切换的大模型。")
    lines.append("要求：用**一句自然口语**回答，三十个字以内，不要念模块名和英文代号。")
    return ActionResponse(Action.REQLLM, "\n".join(lines), None)


# ══════════════════════════════════════════════════════════════
# 工具 2：切换大脑
# ══════════════════════════════════════════════════════════════
switch_llm_desc = {
    "type": "function",
    "function": {
        "name": "switch_llm",
        "description": (
            "切换小智自己使用的大模型（大脑/后台模型）。"
            "当主人说「把大脑切到通义千问 / 换成 DeepSeek / 换个模型」时使用。"
            "切换会立刻生效并记住，主人下次说话就用新大脑了。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "目标模型的模块名。可选："
                        "DeepSeekLLM（DeepSeek 云端）/ QwenLLM（通义千问）/"
                        "OllamaLLM（本地不出网）/ DeepSeekV4LLM / DoubaoLLM（豆包）"
                    ),
                }
            },
            "required": ["name"],
        },
    },
}


@register_function("switch_llm", switch_llm_desc, ToolType.SYSTEM_CTL)
def switch_llm(conn, name: str = ""):
    """切换 LLM。SYSTEM_CTL 类型会传 conn —— 拿到它才能改内存里的实例。"""
    name = (name or "").strip()

    # ① 主人可能说的是中文名（"通义千问"），做别名映射
    alias = {
        "通义千问": "QwenLLM", "千问": "QwenLLM", "qwen": "QwenLLM",
        "阿里": "QwenLLM", "qwen-plus": "QwenLLM",
        "deepseek": "DeepSeekLLM", "深度求索": "DeepSeekLLM",
        "本地": "OllamaLLM", "ollama": "OllamaLLM", "本地模型": "OllamaLLM",
        "v4": "DeepSeekV4LLM", "deepseek v4": "DeepSeekV4LLM",
        "豆包": "DoubaoLLM", "doubao": "DoubaoLLM",
    }
    if name in alias:
        name = alias[name]
    # ② 模糊匹配（大小写/包含）
    profs = _profiles()
    if name not in profs:
        cand = [k for k in list(profs) + ["DeepSeekLLM", "DoubaoLLM"]
                if name.lower() in k.lower() or k.lower() in name.lower()]
        if len(cand) == 1:
            name = cand[0]
        elif len(cand) > 1:
            names = "、".join(_friendly(c) for c in cand)
            return ActionResponse(
                Action.REQLLM,
                f"「{name}」有多个可能：{names}。请主人说具体点，比如「把大脑切到通义千问」。",
                None)
        else:
            avail = "、".join(_friendly(k) for k in profs) or "（配置里一个都没有）"
            return ActionResponse(
                Action.REQLLM,
                f"没有叫「{name}」的大脑。现在能用的是：{avail}。",
                None)

    cur = _read_current()
    if name == cur:
        return ActionResponse(
            Action.REQLLM,
            f"现在本来就是「{_friendly(name)}」，不用切。用一句话告诉主人，三十个字以内。",
            None)

    # ③ key 能不能用
    seg = profs.get(name) or {}
    if not _has_usable_key(seg):
        lab = _friendly(name)
        if seg.get("need_key"):
            where = {"QwenLLM": "阿里云百炼 bailian.console.aliyun.com",
                     "DoubaoLLM": "火山引擎 console.volcengine.com/ark"}.get(
                         name, "对应的官网")
            tip = f"它的 API Key 还没配，需要先去 {where} 申请一个"
        else:
            tip = f"它需要本地先装好模型（Ollama 的要先 ollama pull {seg.get('model_name','')}）"
        return ActionResponse(
            Action.REQLLM,
            f"切不了 —— {tip}。请用一句话把这句话转告主人，四十字以内。",
            None)

    # ④ 先试着建实例 —— 建不起来就别写配置（避免留下坏配置）
    try:
        inst = _build_llm(name)
    except Exception as e:
        logger.bind(tag=TAG).error(f"构建 {name} 失败: {e}")
        return ActionResponse(
            Action.REQLLM,
            f"切换失败：{_friendly(name)} 起不来（{str(e)[:60]}）。"
            f"请用一句话告诉主人，三十个字以内。",
            None)

    # ⑤ 写配置（持久化）
    ok, why = _write_current(name)
    if not ok:
        return ActionResponse(Action.REQLLM,
                              f"切换失败：{why}。请用一句话告诉主人。", None)

    # ⑤b ★ 补写 LLM: 段的同名条目。
    # 没有这一步，"切换"就是**把后台搞坏**：切到引用式档案时重启才起得来，
    # 切到完整式档案（如 QwenLLM）时 `config["LLM"][name]` 不存在 →
    # 重启直接 KeyError 起不来（2026-10-03 真实踩到）。
    sec_ok, sec_msg = _ensure_llm_section(name)
    if not sec_ok:
        # 补不进去就把 selected_module 改回去，绝不留一个"重启就崩"的配置
        _write_current(cur)
        logger.bind(tag=TAG).error(f"补写 LLM 段失败，已回滚切回 {cur}: {sec_msg}")
        return ActionResponse(
            Action.REQLLM,
            f"切换失败：{sec_msg}。已保持原来的大脑不变。"
            f"请用一句话告诉主人，三十个字以内。",
            None,
        )
    if sec_msg.startswith("已补写"):
        logger.bind(tag=TAG).info(f"切模型补写配置: {sec_msg}")

    # ⑥ ★ 换内存实例（当前会话立刻生效，不用重启）
    live = False
    try:
        if conn is not None and getattr(conn, "llm", None) is not None:
            conn.llm = inst
            conn.selected_module_str = (conn.selected_module_str or "").replace(
                cur, name) if cur in (conn.selected_module_str or "") else \
                (conn.selected_module_str + f"|LLM:{name}")
            live = True
    except Exception as e:
        logger.bind(tag=TAG).error(f"换内存实例失败（配置已写，重启后生效）: {e}")

    logger.bind(tag=TAG).info(
        f"LLM 已切换: {cur} -> {name}（立即生效={live}）")

    model = seg.get("model_name", "")
    tail = "，不用重启" if live else "，重启后台后生效"
    return ActionResponse(
        Action.REQLLM,
        f"已切换：你现在用「{_friendly(name)}」"
        + (f"（{model}）" if model else "")
        + f"，从「{_friendly(cur)}」切过来的{tail}。\n"
        f"要求：用**一句自然口语**告诉主人切换成功和新大脑名字，"
        f"不要念模块名和英文代号，三十个字以内。",
        None,
    )
