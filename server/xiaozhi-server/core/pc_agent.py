# -*- coding: utf-8 -*-
"""
pc_agent · 本机任务执行器（服务端大脑的「手」）
================================================================

解决什么问题
------------
小智说「帮我在电脑上查明天北京天气，写个 txt 放桌面」时：
  · 服务端 pc_control 插件只是把任务**写进** `bridge/inbox/`（凭它没有手）
  · 原来的执行者是 WorkBuddy 的定时 automation，**最快也要等下一次触发**（可能一小时）
  · 结果就是用户体感「他说丢给助理了，但啥也没发生」

本模块补上缺的那一环：**由自建后台自己把任务干完**。
后台本来就常驻、本来就有 DeepSeek key，所以：
  inbox 有新任务 → 本模块用 DeepSeek + 本机工具执行 → 写回 done/ → watcher 主动播报

时延从「最长一小时」降到「几秒~几十秒」。

设计要点
--------
1. `execute_task_file()` 是**同步**的（内部用 openai 同步客户端），
   由 notify_plugin 的 watcher 用 `asyncio.to_thread()` 调用，绝不阻塞事件循环。
2. 任务状态机：pending → running → done。
   **进入 running 先落盘**，这样进程崩了也不会重复执行（人工可重置）。
3. 只做任务要求的事；危险命令有硬拦截（用户虽授权全权限，但格式化/删系统目录这类
   一旦误判就是不可逆灾难，宁可拒绝并说明）。
4. 结果写成口语化短句（≤260 字），因为它会被 TTS 念出来。

用法（一般由 watcher 自动调用）
------------------------------
    from core.pc_agent import execute_task_file
    execute_task_file(Path("bridge/inbox/task-xxx.md"))
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

try:
    from config.logger import setup_logging
except Exception:  # 允许脱离服务端单独调试
    import logging

    def setup_logging():
        logging.basicConfig(level=logging.INFO)
        return logging.getLogger("pc_agent")

TAG = "pc_agent"
logger = setup_logging()

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
SERVER_DIR = Path(__file__).resolve().parents[1]        # server/xiaozhi-server
PROJECT_ROOT = Path(__file__).resolve().parents[3]      # <project>
DATA_CONFIG = SERVER_DIR / "data" / ".config.yaml"


def _bridge_dir() -> Path:
    env = os.environ.get("XZ_BRIDGE_DIR")
    return Path(env) if env else (PROJECT_ROOT / "bridge")


HOME = Path.home()

# 常见中文路径别名（Windows 上 Desktop/Documents 可能被 OneDrive 重定向，故多探几个）
_PATH_ALIAS = {
    "桌面": [HOME / "Desktop", HOME / "OneDrive" / "Desktop"],
    "desktop": [HOME / "Desktop", HOME / "OneDrive" / "Desktop"],
    "文档": [HOME / "Documents", HOME / "OneDrive" / "Documents"],
    "documents": [HOME / "Documents", HOME / "OneDrive" / "Documents"],
    "下载": [HOME / "Downloads"],
    "downloads": [HOME / "Downloads"],
    "图片": [HOME / "Pictures"],
    "pictures": [HOME / "Pictures"],
}


def _pick_existing(cands):
    for c in cands:
        try:
            if c.exists():
                return c
        except Exception:
            continue
    return cands[0]


def _resolve_path(raw: str) -> Path:
    """把「桌面/xx.txt」这类口语路径解析成真实绝对路径。"""
    p = (raw or "").strip().strip('"').strip("'")
    if not p:
        return HOME
    low = p.lower()
    for key, cands in _PATH_ALIAS.items():
        if low == key.lower():
            return _pick_existing(cands)
        for sep in ("\\", "/"):
            prefix = key + sep
            if low.startswith(prefix.lower()):
                rest = p[len(key) + 1:]
                return _pick_existing(cands) / rest
    p = os.path.expandvars(os.path.expanduser(p))
    return Path(p)


# --------------------------------------------------------------------------
# 执行安全闸（宁可拒绝，也不做不可逆的事）
# --------------------------------------------------------------------------
_DANGEROUS_PATTERNS = [
    r"\bformat\b.*[/\s][a-z]:",     # format C:
    r"\bdiskpart\b",
    r"\bdel\b.*/[sfq].*\b[a-z]:\s*$",  # del /f /s /q C:
    r"rm\s+-rf\s+/(\s|$)",
    r"remove-item.*-recurse.*-force.*[a-z]:\\\s*$",
    r"\bshutdown\b",                # 关机/重启（要用户明确要求才允许，见下）
    r"\bbcdedit\b",
    r"cipher\s+/w",
    r"\bmkfs\b",
    r"reg\s+delete\s+hklm",
]
_DANGEROUS_RE = re.compile("|".join(_DANGEROUS_PATTERNS), re.IGNORECASE)


def _guard(command: str) -> str:
    """返回空串表示放行，否则返回拒绝原因。"""
    if not command or not command.strip():
        return "命令为空"
    if _DANGEROUS_RE.search(command):
        return "该命令包含不可逆的高危操作（格式化/删除系统目录/关机等），已拦截"
    return ""


# --------------------------------------------------------------------------
# 本机工具实现
# --------------------------------------------------------------------------
def _decode(b: bytes) -> str:
    if not b:
        return ""
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", "replace")


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "\n…（已截断）"


def tool_run_shell(command: str, timeout: int = 60) -> str:
    reason = _guard(command)
    if reason:
        return "[拒绝] " + reason
    try:
        timeout = max(1, min(int(timeout or 60), 300))
    except Exception:
        timeout = 60
    try:
        p = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout,
            cwd=str(HOME),
        )
        parts = ["exit=%d" % p.returncode]
        out = _decode(p.stdout)
        err = _decode(p.stderr)
        if out:
            parts.append("[stdout]\n" + _clip(out, 4000))
        if err:
            parts.append("[stderr]\n" + _clip(err, 2000))
        if len(parts) == 1:
            parts.append("（无输出）")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return "[超时] 命令超过 %d 秒未结束，已终止" % timeout
    except Exception as e:
        return "[失败] %s: %s" % (type(e).__name__, e)


def tool_run_powershell(script: str, timeout: int = 60) -> str:
    reason = _guard(script)
    if reason:
        return "[拒绝] " + reason
    try:
        timeout = max(1, min(int(timeout or 60), 300))
    except Exception:
        timeout = 60
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=timeout,
            cwd=str(HOME),
        )
        parts = ["exit=%d" % p.returncode]
        out = _decode(p.stdout)
        err = _decode(p.stderr)
        if out:
            parts.append("[stdout]\n" + _clip(out, 4000))
        if err:
            parts.append("[stderr]\n" + _clip(err, 2000))
        if len(parts) == 1:
            parts.append("（无输出）")
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return "[超时] 脚本超过 %d 秒未结束，已终止" % timeout
    except Exception as e:
        return "[失败] %s: %s" % (type(e).__name__, e)


def tool_read_text_file(path: str, max_chars: int = 4000) -> str:
    p = _resolve_path(path)
    if not p.exists():
        return "[失败] 文件不存在：%s" % p
    if p.is_dir():
        return "[失败] %s 是目录，请用 list_dir" % p
    try:
        try:
            max_chars = max(200, min(int(max_chars or 4000), 20000))
        except Exception:
            max_chars = 4000
        txt = p.read_text(encoding="utf-8", errors="replace")
        return "文件 %s 内容：\n%s" % (p, _clip(txt, max_chars))
    except Exception as e:
        return "[失败] 读取失败: %s" % e


def tool_write_text_file(path: str, content: str) -> str:
    p = _resolve_path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # utf-8-sig：带 BOM，Windows 记事本不会乱码
        p.write_text(content or "", encoding="utf-8-sig")
        return "已写入 %s（%d 字符）" % (p, len(content or ""))
    except Exception as e:
        return "[失败] 写入失败: %s" % e


def tool_list_dir(path: str = "") -> str:
    p = _resolve_path(path or str(HOME))
    if not p.exists():
        return "[失败] 路径不存在：%s" % p
    if not p.is_dir():
        return "[失败] %s 不是目录" % p
    try:
        items = []
        for it in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                if it.is_dir():
                    items.append("[目录] " + it.name)
                else:
                    items.append("[文件] %s  (%.1f KB)" % (it.name, it.stat().st_size / 1024))
            except Exception:
                items.append("[?] " + it.name)
        if not items:
            return "%s 是空目录" % p
        return "%s 下有 %d 项：\n%s" % (p, len(items), "\n".join(items[:80]))
    except Exception as e:
        return "[失败] 列目录失败: %s" % e


def tool_open_path(target: str) -> str:
    """打开文件 / 程序 / 文件夹 / URL（等价于双击）。"""
    if not target or not target.strip():
        return "[失败] 目标为空"
    t = target.strip().strip('"')
    try:
        if re.match(r"^(https?|mailto|kugou|music):", t, re.IGNORECASE):
            os.startfile(t)
            return "已打开 %s" % t
        p = _resolve_path(t)
        if p.exists():
            os.startfile(str(p))
            return "已打开 %s" % p
        # 不在本地路径里 → 当成 PATH 中的程序名
        os.startfile(t)
        return "已尝试打开 %s" % t
    except Exception as e:
        return "[失败] 打开失败: %s" % e


def tool_web_fetch(url: str, max_chars: int = 4000) -> str:
    import urllib.request

    if not url or not url.strip().lower().startswith(("http://", "https://")):
        return "[失败] 请给 http/https 开头的完整网址"
    try:
        try:
            max_chars = max(300, min(int(max_chars or 4000), 20000))
        except Exception:
            max_chars = 4000
        req = urllib.request.Request(
            url.strip(),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
        text = _decode(raw)
        # 粗暴去标签，够模型读懂就行
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;?", " ", text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        return "网页 %s 正文（已去标签）：\n%s" % (url, _clip(text, max_chars))
    except Exception as e:
        return "[失败] 取网页失败: %s: %s" % (type(e).__name__, e)


def tool_list_processes() -> str:
    return tool_run_shell(
        'tasklist /FO CSV /NH | findstr /I "." ', timeout=30
    )


# ---------------------------------------------------------------- 联网搜索（免 key）
def tool_search_web(query: str, max_results: int = 5) -> str:
    """免 API Key 的联网搜索：走必应中国 HTML 端点（实测本机可达；
    DuckDuckGo 被墙、Metaso/Tavily 需要付费 key，故用必应兜底）。"""
    import urllib.parse
    import urllib.request

    q = (query or "").strip()
    if not q:
        return "[失败] 搜索词为空"
    try:
        n = max(1, min(int(max_results or 5), 10))
    except Exception:
        n = 5

    url = "https://cn.bing.com/search?q=" + urllib.parse.quote(q) + "&setlang=zh-CN&ensearch=0"
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
                ),
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            html = _decode(r.read())
    except Exception as e:
        return "[失败] 搜索失败: %s: %s" % (type(e).__name__, e)

    items = []
    for m in re.finditer(r'<li class="b_algo".*?</li>', html, re.S):
        block = m.group(0)
        tm = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not tm:
            continue
        link = tm.group(1)
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", tm.group(2))).strip()
        snippet = ""
        sm = re.search(r"<p[^>]*>(.*?)</p>", block, re.S)
        if sm:
            snippet = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", sm.group(1))).strip()
        items.append("%d. %s\n   链接：%s\n   摘要：%s" % (len(items) + 1, title, link, snippet[:220]))
        if len(items) >= n:
            break

    if not items:
        return "没有搜到结果（可能被反爬拦了，或关键词太偏）。可换关键词再试。"
    return "必应搜索结果（关键词：%s）：\n%s" % (q, "\n".join(items))


# ---------------------------------------------------------------- 按名找文件
def tool_search_files(path: str = "", pattern: str = "*", max_results: int = 60) -> str:
    """在指定目录下递归查找文件名匹配 pattern（通配符）的文件。"""
    base = _resolve_path(path or str(HOME))
    if not base.exists():
        return "[失败] 路径不存在：%s" % base
    if not base.is_dir():
        return "[失败] %s 不是目录" % base
    try:
        n = max(1, min(int(max_results or 60), 200))
    except Exception:
        n = 60
    pat = (pattern or "*").strip().lstrip("*") or "*"
    if not pat.startswith("*") and "." not in pat:
        pat = "*" + pat + "*"
    elif not pat.startswith("*"):
        pat = "*" + pat

    hits = []
    try:
        for p in base.rglob(pat):
            try:
                if p.is_file():
                    hits.append("%s  (%.1f KB)" % (p, p.stat().st_size / 1024))
            except Exception:
                continue
            if len(hits) >= n:
                break
    except Exception as e:
        return "[失败] 查找失败: %s" % e

    if not hits:
        return "在 %s 下没找到匹配「%s」的文件。" % (base, pat)
    return "在 %s 下按「%s」找到 %d 个文件：\n%s" % (base, pat, len(hits), "\n".join(hits))


# ---------------------------------------------------------------- 文件归类（安全版）
_ORGANIZE_CATEGORIES = {
    "文档": (".doc", ".docx", ".pdf", ".txt", ".md", ".rtf", ".odt", ".wps", ".xls", ".xlsx",
             ".ppt", ".pptx", ".csv", ".et", ".dps"),
    "图片": (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico", ".svg", ".heic"),
    "音频": (".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".wma", ".ape"),
    "视频": (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".rmvb", ".m4v"),
    "压缩包": (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"),
    "程序": (".exe", ".msi", ".bat", ".cmd", ".ps1", ".apk", ".appx"),
    "代码": (".py", ".c", ".cpp", ".cc", ".h", ".hpp", ".js", ".ts", ".java", ".go", ".rs",
             ".json", ".yaml", ".yml", ".xml", ".html", ".htm", ".css", ".sh", ".ino"),
}


def _category_of(name: str, mode: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if mode == "by_ext":
        return (ext.lstrip(".") or "无扩展名").upper()
    for cat, exts in _ORGANIZE_CATEGORIES.items():
        if ext in exts:
            return cat
    return "其他"


def tool_organize_dir(path: str = "", mode: str = "by_type",
                      apply: bool = False, max_files: int = 50) -> str:
    """把目录里的文件按类型/扩展名归入子文件夹。

    安全设计（重要）：
      · 默认 apply=False —— **只出计划、不动任何文件**；必须显式 apply=True 才真的搬。
      · 只搬**文件**，绝不动子目录、绝不删除、绝不覆盖（重名自动加序号）。
      · 跳过隐藏/系统文件（以 . 或 ~$ 开头）。
      · 单次最多搬 max_files（默认 50）个，防止一把梭。
    """
    base = _resolve_path(path or str(HOME))
    if not base.exists() or not base.is_dir():
        return "[失败] 目录不存在：%s" % base
    try:
        cap = max(1, min(int(max_files or 50), 200))
    except Exception:
        cap = 50
    mode = "by_ext" if str(mode).strip() == "by_ext" else "by_type"

    # 收集候选（只文件、只顶层、跳过隐藏/临时）
    cands = []
    for it in sorted(base.iterdir()):
        try:
            if it.is_dir() or it.is_symlink():
                continue
            name = it.name
            if name.startswith(".") or name.startswith("~$"):
                continue
            cands.append(it)
        except Exception:
            continue

    if not cands:
        return "%s 顶层没有可归类的文件（已跳过子目录与隐藏文件）。" % base

    plan = {}
    for f in cands:
        plan.setdefault(_category_of(f.name, mode), []).append(f)
    total = sum(len(v) for v in plan.values())

    lines = ["%s 顶层共 %d 个文件，按「%s」归类计划：" % (
        base, total, "扩展名" if mode == "by_ext" else "类型")]
    for cat in sorted(plan):
        lines.append("  【%s】%d 个：%s" % (
            cat, len(plan[cat]),
            "、".join(x.name for x in plan[cat][:6]) + ("…" if len(plan[cat]) > 6 else "")))
    plan_text = "\n".join(lines)

    if not apply:
        return (plan_text + "\n\n（这是**计划**，还没有动任何文件。"
                "主人确认后再用 apply=true 执行。）")

    moved, errors = 0, []
    for cat, files in plan.items():
        sub = base / cat
        try:
            sub.mkdir(exist_ok=True)
        except Exception as e:
            errors.append("建目录 %s 失败: %s" % (cat, e))
            continue
        for f in files:
            if moved >= cap:
                break
            target = sub / f.name
            k = 1
            while target.exists():
                target = sub / ("%s_%d%s" % (f.stem, k, f.suffix))
                k += 1
                if k > 200:
                    break
            try:
                f.replace(target)
                moved += 1
            except Exception as e:
                errors.append("移动 %s 失败: %s" % (f.name, e))
        if moved >= cap:
            break

    tail = "已移动 %d 个文件" % moved
    if moved >= cap:
        tail += "（达到本次上限 %d，其余未动，可再跑一次）" % cap
    if errors:
        tail += "；%d 个失败：%s" % (len(errors), "；".join(errors[:5]))
    return plan_text + "\n\n" + tail + "。"


# --------------------------------------------------------------------------
# 桌面层工具（2026-10-03）：窗口 / 剪贴板 / 键盘 / 截屏
# 底层都在 core/pc_desktop.py，这里只做「LLM 能调」的薄封装。
# --------------------------------------------------------------------------
def _desktop():
    from core import pc_desktop

    return pc_desktop


def tool_list_windows(limit: int = 40):
    """列出当前打开的窗口（标题 + 是否最小化）。"""
    r = _desktop().list_windows(limit=int(limit or 40))
    if not r.get("ok"):
        return "[失败] " + str(r.get("msg"))
    ws = r.get("windows") or []
    if not ws:
        return "当前没有打开的窗口。"
    lines = []
    for w in ws:
        flag = "（最小化）" if w.get("minimized") else ""
        lines.append(f"- {w['title']}{flag}")
    return f"共 {r['count']} 个窗口：\n" + "\n".join(lines)


def tool_window_action(title: str = "", action: str = "focus", hwnd: int = 0):
    """对窗口做动作：focus(置前)/minimize/maximize/restore/close。"""
    r = _desktop().window_action(title or "", action or "focus", int(hwnd or 0))
    if not r.get("ok"):
        avail = r.get("available")
        msg = "[失败] " + str(r.get("msg"))
        if avail:
            msg += "；当前窗口有：" + "、".join(avail[:10])
        return msg
    return "[成功] " + str(r.get("msg"))


def tool_clipboard(mode: str = "get", text: str = ""):
    """读(get)或写(set)剪贴板。"""
    mode = (mode or "get").strip().lower()
    if mode == "set":
        r = _desktop().clipboard_set(text or "")
    else:
        r = _desktop().clipboard_get()
        if r.get("ok"):
            t = r.get("text") or ""
            return t if t else "（剪贴板是空的）"
    return ("[成功] " + r["msg"]) if r.get("ok") else "[失败] " + str(r.get("msg"))


def tool_send_keys(keys: str = "", title: str = "", delay: float = 0.05):
    """发组合键或打字，如 ctrl+c / alt+tab / win+d / enter。可先 title 置前某窗口。"""
    r = _desktop().send_keys(keys or "", float(delay or 0.05), title or "")
    return ("[成功] " + r["msg"]) if r.get("ok") else "[失败] " + str(r.get("msg"))


def tool_screenshot(save_to: str = "", title: str = ""):
    """截屏存 PNG。save_to 留空存桌面；title 指定则只截那个窗口。"""
    r = _desktop().screenshot(save_to or "", 0, title or "")
    if r.get("ok"):
        return f"[成功] 已截图 {r['size']}，存到 {r['path']}"
    return "[失败] " + str(r.get("msg"))


TOOL_IMPLS = {
    "run_shell": tool_run_shell,
    "run_powershell": tool_run_powershell,
    "read_text_file": tool_read_text_file,
    "write_text_file": tool_write_text_file,
    "list_dir": tool_list_dir,
    "open_path": tool_open_path,
    "web_fetch": tool_web_fetch,
    "list_processes": tool_list_processes,
    "search_web": tool_search_web,
    "search_files": tool_search_files,
    "organize_dir": tool_organize_dir,
    # ---- 桌面层（2026-10-03 新增，见 core/pc_desktop.py）----
    "list_windows": tool_list_windows,
    "window_action": tool_window_action,
    "clipboard": tool_clipboard,
    "send_keys": tool_send_keys,
    "screenshot": tool_screenshot,
}

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "在用户电脑上执行一条 Windows 命令（cmd 语法），返回 exit code 与输出。"
                "适合：打开软件、查看系统信息、跑脚本、批量处理文件。"
                "多步操作请拆成多条命令逐条执行。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "完整命令，例如 start notepad.exe"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 60，最大 300"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_powershell",
            "description": (
                "执行一段 PowerShell 脚本，返回输出。比 cmd 更强："
                "查进程、查文件属性、处理文本、调 .NET / COM 等都用它。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {"type": "string", "description": "完整的 PowerShell 脚本内容"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 60，最大 300"},
                },
                "required": ["script"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_text_file",
            "description": "读取一个文本文件的内容（utf-8，自动容错）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径，支持「桌面/xx.txt」这种口语写法"},
                    "max_chars": {"type": "integer", "description": "最多读多少字符，默认 4000"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_text_file",
            "description": (
                "把文本写入文件（不存在则创建，父目录自动建）。"
                "写 .txt 给人看的内容时用它。编码为 UTF-8-BOM，Windows 记事本不乱码。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径，支持「桌面/北京天气.txt」"},
                    "content": {"type": "string", "description": "要写入的完整文本内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出目录下的文件与子目录（含大小）。找文件时先用它探查，不要猜路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，支持「桌面」，留空为用户主目录"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_path",
            "description": "打开文件 / 文件夹 / 程序 / 网址（等价于双击）。用于「打开某个软件」。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "description": "文件路径、程序名或 http(s) 网址"},
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取一个网页的文本内容（自动去 HTML 标签）。查资料、拿天气、看公告用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http/https 开头的完整网址"},
                    "max_chars": {"type": "integer", "description": "最多返回多少字符，默认 4000"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_processes",
            "description": "列出当前正在运行的进程（判断某软件是否已经打开时用）。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "免 key 联网搜索（必应中国）。查资料、找答案、了解新闻时用它，"
                "比 web_fetch 方便（不用先知道网址）。拿到结果后可再用 web_fetch 打开某条细看。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或问题"},
                    "max_results": {"type": "integer", "description": "返回条数，默认 5，最多 10"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "在某个目录下按文件名**递归**查找文件（支持 * 通配符）。"
                "主人说「帮我找找那个 xx 文件」时用它，比 list_dir 只列顶层强。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "在哪个目录下找，支持「桌面」，留空为整个用户主目录"},
                    "pattern": {"type": "string", "description": "文件名通配，如 *.pdf、*报表*、合同*"},
                    "max_results": {"type": "integer", "description": "最多返回几个，默认 60"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "organize_dir",
            "description": (
                "把目录里的文件按类型归入子文件夹（文档/图片/音频/视频/压缩包/程序/代码/其他）。"
                "**重要：默认 apply=false，只出计划不搬文件。**"
                "规矩：先以 apply=false 出计划并把计划汇报给主人，"
                "只有当主人已经明确说「确认 / 动手吧 / 直接整理」时才用 apply=true 真正执行。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "要整理的目录，支持「桌面」"},
                    "mode": {
                        "type": "string",
                        "enum": ["by_type", "by_ext"],
                        "description": "by_type=按大类（默认），by_ext=按扩展名细分",
                    },
                    "apply": {"type": "boolean", "description": "是否真的搬文件；默认 false（只出计划）"},
                    "max_files": {"type": "integer", "description": "单次最多搬几个，默认 50"},
                },
                "required": ["path"],
            },
        },
    },
    # ---------- 桌面层（2026-10-03 新增）----------
    {
        "type": "function",
        "function": {
            "name": "list_windows",
            "description": (
                "列出当前打开的窗口标题。想对某个窗口操作但拿不准确切标题时，先调这个。"
                "主人说「我开了哪些程序」也可以用它回答。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "最多列几个，默认 40"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_action",
            "description": (
                "对窗口做动作：focus=切到前台（最常用）、minimize=最小化、"
                "maximize=最大化、restore=还原、close=关闭。"
                "主人说「把某某窗口切过来」「关掉那个窗口」「最小化一下」就用它。"
                "标题支持模糊匹配，「酷狗」能匹配到「酷狗音乐」。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "窗口标题（模糊匹配即可）"},
                    "action": {
                        "type": "string",
                        "enum": ["focus", "minimize", "maximize", "restore", "close"],
                        "description": "动作，默认 focus",
                    },
                    "hwnd": {"type": "integer", "description": "可选，直接用 list_windows 拿到的窗口句柄"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clipboard",
            "description": (
                "读(get)或写(set)系统剪贴板。"
                "主人说「复制一段话」「把剪贴板里的内容给我」时用它。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["get", "set"], "description": "get=读，set=写"},
                    "text": {"type": "string", "description": "mode=set 时要写入的文本"},
                },
                "required": ["mode"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_keys",
            "description": (
                "模拟键盘：发组合键或打字。写法如 ctrl+c、ctrl+v、alt+tab、win+d、enter、f5。"
                "可以先用 title 把某个窗口切到前台再按键。"
                "注意：这是全局按键，务必先确认目标窗口在前台。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "string", "description": "组合键，如 ctrl+c；或要打的字"},
                    "title": {"type": "string", "description": "可选，先把这个窗口切到前台"},
                    "delay": {"type": "number", "description": "按键间隔秒，默认 0.05"},
                },
                "required": ["keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": (
                "截屏并存成 PNG。save_to 留空就存桌面（文件名带时间戳）。"
                "填 title 则只截那个窗口。主人说「截个图」「看看屏幕上有什么」时用它。"
                "截完告诉主人文件存哪了（本工具只能存文件，不能把图给主人看）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "save_to": {"type": "string", "description": "保存路径，留空存桌面"},
                    "title": {"type": "string", "description": "可选，只截这个窗口"},
                },
                "required": [],
            },
        },
    },
]

# --------------------------------------------------------------------------
# 执行器系统提示词
# --------------------------------------------------------------------------
_SYSTEM_PROMPT = """你是主人 Windows 电脑上的执行助理。主人通过语音把一句话任务派给你，你要用工具把它**真正做完**。

铁律：
1. 每一步都必须基于工具的真实返回，**绝不臆造结果**。工具报错就换方法或如实说明失败原因。
2. 完成后用**口语化中文一句话**总结（会被语音念给主人听），不超过 100 字，不要用 Markdown、不要列点、不要括号动作。
3. 只做任务要求的事，不额外折腾主人的电脑。
4. 危险操作（格式化磁盘、删系统目录、关机重启、改注册表关键项）一律拒绝并说明。
5. 需要联网查资料时用 search_web（比 web_fetch 方便，不必先知道网址）；
   拿到某条结果想看细节，再 web_fetch 那个链接。拿不到就如实说拿不到，
   **不要编天气、编股价、编新闻**。
6. 找不到文件时先用 list_dir / search_files 探查，不要猜路径。
7. 「桌面」通常就是 C:\\Users\\zuowe\\Desktop（工具会自动把「桌面/xx」解析成真实路径，直接写中文即可）。
8. 判断某软件是否已经打开，用 list_processes。
9. 效率优先：能一条命令搞定的，不要拆成五条。
10. **整理/归类目录（桌面归类等）**：先调 organize_dir 且 **apply=false** 出一份计划，
    把计划用口语汇报给主人并请他确认；**不要**未经确认就 apply=true 动主人的文件。
    只有主人已明确说「确认/动手吧/直接整理」时，才 apply=true 执行。
11. **窗口/剪贴板/键盘/截屏**这些桌面操作用 list_windows / window_action / clipboard /
    send_keys / screenshot，不要绕回 run_shell 去搓命令（不稳也慢）。
    关软件优先 window_action(action="close")（温和，给程序保存提示的机会），
    实在关不掉再用 run_shell 的 taskkill。
12. send_keys 是全局按键，按之前先 window_action(focus) 确认目标窗口在前台，别按错地方。

完成后停止调用工具，直接输出给主人的那句话。"""


def _load_llm_conf():
    """从 data/.config.yaml 读**执行器专用**的 LLM 配置 + 步数预算。

    ★ 2026-10-04 主人要求：执行器默认走便宜模型（deepseek-v4-flash），
      别跟主对话共用那个贵的。三档预算也改成可配（原来全写死在源码里）。

    取值优先级：
      ① `executor.llm_profile` 指定的 profile（先查顶层 `llm_profiles` 的完整式，
         再查 `LLM:` 段）
      ② `selected_module.LLM`（主对话那个，兼容旧行为）
      ③ `LLM:` 段里第一个带 api_key 的
    """
    import yaml

    with open(DATA_CONFIG, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    ex = cfg.get("executor") or {}
    profiles = cfg.get("llm_profiles") or {}
    llm_block = cfg.get("LLM") or {}

    conf = {}
    name = ex.get("llm_profile")
    if name:
        # ⚠️ llm_profiles 分「完整式」和「引用式」，只有完整式自带 api_key
        conf = dict(profiles.get(name) or llm_block.get(name) or {})
    if not conf.get("api_key"):
        sel = (cfg.get("selected_module") or {}).get("LLM")
        conf = dict(llm_block.get(sel) or {})
    if not conf.get("api_key"):
        for k, v in llm_block.items():
            if isinstance(v, dict) and v.get("api_key"):
                conf = dict(v)
                break

    def _int(v, default):
        try:
            return int(v)
        except Exception:
            return default

    return {
        "base_url": conf.get("base_url") or "https://api.deepseek.com/v1",
        "api_key": conf.get("api_key") or "",
        "model": conf.get("model_name") or "deepseek-chat",
        "temperature": conf.get("temperature", 0.3),
        # ---- 步数预算（原为硬编码 14 / 1200 / 无墙钟）----
        "max_tokens": _int(ex.get("max_tokens"), 4096),
        "max_rounds": _int(ex.get("max_rounds"), 20),
        "max_task_sec": _int(ex.get("max_task_sec"), 240),
    }


def _parse_task_file(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    status = "pending"
    m = re.search(r"^-\s*状态：\s*(.+)$", text, re.M)
    if m:
        status = m.group(1).strip()
    content = ""
    if "## 任务内容" in text:
        content = text.split("## 任务内容", 1)[1]
        if "## 执行结果" in content:
            content = content.split("## 执行结果", 1)[0]
        content = content.strip()
    return status, content


# --------------------------------------------------------------------------
# 工作空间：主人说「在某某项目里帮我…」时，把活落进那个目录
# --------------------------------------------------------------------------
_WORKSPACES_ROOT = Path(r"C:\Users\zuowe\WorkBuddy")


def _read_workspace(path: Path) -> str:
    """读任务单里可选的「工作空间：」行（由 pc_control 的 workspace 参数写入）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^-\s*工作空间：\s*(.+)$", text, re.M)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def _resolve_workspace(name: str) -> str:
    """把主人嘴里的空间名解析成真实目录。

    支持三种写法：
      1. 绝对路径（本来就是目录）      -> 原样
      2. WorkBuddy 空间目录名/片段      -> C:\\Users\\zuowe\\WorkBuddy\\<匹配>
      3. 认不出来                       -> 返回空（交给模型自己找，别瞎猜）
    """
    name = (name or "").strip().strip("「」\"'")
    if not name:
        return ""
    try:
        p = Path(name)
        if p.is_absolute() and p.is_dir():
            return str(p)
    except Exception:
        pass
    try:
        if _WORKSPACES_ROOT.is_dir():
            cands = [d for d in _WORKSPACES_ROOT.iterdir()
                     if d.is_dir() and name.lower() in d.name.lower()]
            if cands:
                cands.sort(key=lambda d: d.stat().st_mtime, reverse=True)
                return str(cands[0])
    except Exception:
        pass
    return ""


def _rewrite_status(path: Path, status: str, result: str = "", steps=None, ms=None):
    """就地重写任务单（保持与 bridge/server_agent.py 的格式一致）。

    ★ 2026-10-04 补遥测：`run_agent` 早就返回 steps/ms，但真实入口只写 result，
      导致 42 张任务单**没有一张记录步数和耗时**——想校准步数预算都没数据。
    """
    tid = path.stem.replace("task-", "")
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"^- 状态：.*$", text, re.M)
    if m:
        text = text[: m.start()] + "- 状态：%s" % status + text[m.end():]
    else:
        text = text.replace("# 任务 %s\n" % tid, "# 任务 %s\n\n- 状态：%s\n" % (tid, status))

    # 步数 / 耗时：写在状态行后面，已存在就覆盖
    meta = []
    if steps is not None:
        meta.append(("步数", str(steps)))
    if ms:
        meta.append(("耗时", "%dms" % int(ms)))
    if meta:
        # 先清掉旧的遥测行（避免重复插入），再统一写在状态行后面
        for k, _ in meta:
            text = re.sub(r"\n-\s*%s：[^\n]*" % k, "", text, flags=re.M)
        m2 = re.search(r"^-\s*状态：.*$", text, re.M)
        if m2:
            ins = "".join("\n- %s：%s" % kv for kv in meta)
            text = text[: m2.end()] + ins + text[m2.end():]

    if "## 执行结果" in text:
        head = text.split("## 执行结果", 1)[0]
        text = head + "## 执行结果\n\n" + (result or "") + "\n"
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# 主循环
# --------------------------------------------------------------------------
def run_agent(task_text: str, max_rounds=None) -> dict:
    """用大模型 + 本机工具把 task_text 做完，返回 {ok, result, steps, ms}。

    ★ 2026-10-04 三项预算改造（原代码全写死，导致复合任务必败）：
      - `max_rounds`  默认取配置 `executor.max_rounds`（默认 20，原写死 14）
      - `max_tokens`  默认取配置 `executor.max_tokens`（默认 4096，原写死 1200）
      - `max_task_sec` **全局墙钟**（默认 240s，原来完全没有 → 理论最长能卡 91 分钟）
    """
    from openai import OpenAI

    conf = _load_llm_conf()
    if not conf["api_key"]:
        return {"ok": False, "result": "后台没配大模型 API Key，我暂时干不了活。", "steps": 0, "ms": 0}

    client = OpenAI(api_key=conf["api_key"], base_url=conf["base_url"], timeout=90)

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "任务：" + task_text},
    ]
    t0 = time.time()
    steps = 0
    rounds = int(max_rounds or conf["max_rounds"])
    deadline = t0 + conf["max_task_sec"]   # ★ 全局墙钟

    for i in range(rounds):
        # ★ 墙钟：不管卡在 LLM 还是工具，到点就收工，不让它假装还在干
        if time.time() > deadline:
            spent = int(time.time() - t0)
            logger.warning(f"[pc_agent] 墙钟超时 {spent}s（限 {conf['max_task_sec']}s），停在 {steps} 步")
            return {
                "ok": False,
                "result": "跑了 %d 秒还没干完，我先停了（上限 %d 秒），卡在第 %d 步附近。这活得拆小点再来。" % (
                    spent, conf["max_task_sec"], steps),
                "steps": steps,
                "ms": int((time.time() - t0) * 1000),
                "reason": "timeout",
            }

        try:
            resp = client.chat.completions.create(
                model=conf["model"],
                messages=messages,
                tools=TOOLS_SCHEMA,
                tool_choice="auto",
                temperature=conf["temperature"],
                max_tokens=conf["max_tokens"],
            )
        except Exception as e:
            logger.error(f"[pc_agent] LLM 调用失败: {type(e).__name__}: {e}")
            return {
                "ok": False,
                "result": "大脑连不上了（%s），任务没做完。" % type(e).__name__,
                "steps": steps,
                "ms": int((time.time() - t0) * 1000),
            }

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            text = (msg.content or "").strip()
            logger.info(f"[pc_agent] 完成（{steps} 步）：{text[:80]}")
            return {
                "ok": True,
                "result": text or "任务处理完了。",
                "steps": steps,
                "ms": int((time.time() - t0) * 1000),
            }

        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )

        for tc in tool_calls:
            steps += 1
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            fn = TOOL_IMPLS.get(name)
            if fn is None:
                out = "[失败] 没有名为 %s 的工具" % name
            else:
                try:
                    out = fn(**args)
                except TypeError as e:
                    out = "[失败] 参数不对: %s" % e
                except Exception as e:
                    out = "[失败] %s: %s" % (type(e).__name__, e)
            logger.info(
                f"[pc_agent] 步骤{steps} {name}({str(args)[:120]}) -> {str(out)[:160]}"
            )
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": str(out)[:8000]}
            )

    # ★ 2026-10-04：原来不管什么原因都甩同一句「步骤太多，你拆小点再说」，
    #   把「能力边界」和「真·步数耗尽」混成一谈，主人会重复派注定失败的活。
    #   现在至少分开说，且带上真实步数。
    return {
        "ok": False,
        "result": "这活儿跑了 %d 步还没干完（上限 %d 步）。步骤确实多了点，建议拆成两件小事分别说给我听。" % (
            steps, rounds),
        "steps": steps,
        "ms": int((time.time() - t0) * 1000),
        "reason": "steps_exhausted",
    }


# ★ 2026-10-04 B 类失败：**物理上够不到**的活。
#   以前这条只写在提示词里让模型「别派」，模型不听 → 白跑十几步再甩一句「步骤太多」，
#   主人得到的信号是"拆小点再说"，真相是"这事根本干不了"，重复派单只会重复失败。
#   ⚠️ 规则要保守：==打开微信== 是合法任务（电脑版微信），只有**明确出现别的设备**才挡。
_IMPOSSIBLE_RULES = [
    (r"手机|iphone|安卓|android", "我只跑在你这台 Windows 电脑上，够不到手机"),
    (r"平板|ipad", "我只跑在你这台 Windows 电脑上，够不到平板"),
    (r"另一台(电脑|机器|主机)|别的电脑|远程(电脑|桌面|机器)|服务器上|公司电脑",
     "我只能操作本机，够不到别的机器"),
]


def _refuse_if_impossible(task_text: str):
    """判断是不是物理上干不了的活。返回拒绝话术，None = 可以做。"""
    import re as _re
    t = (task_text or "").lower()
    for pat, why in _IMPOSSIBLE_RULES:
        if _re.search(pat, t):
            return ("这事我真做不了：%s。"
                    "能做的替代是——你把要弄的内容发到这台电脑上，我再帮你处理。" % why)
    return None


def execute_task_file(path: Path) -> dict:
    """
    执行一张任务单：
      pending -> （落盘 running）-> 执行 -> done/ + 结果
    返回 {tid, ok, result, ms}
    """
    path = Path(path)
    tid = path.stem.replace("task-", "")
    status, content = _parse_task_file(path)

    if status not in ("pending", ""):
        logger.info(f"[pc_agent] 跳过 {path.name}（状态={status}）")
        return {"tid": tid, "ok": False, "result": "任务状态不是 pending，已跳过", "ms": 0}

    if not content:
        _rewrite_status(path, "failed", "任务内容是空的，没法执行。")
        _move_to_done(path)
        return {"tid": tid, "ok": False, "result": "任务内容为空", "ms": 0}

    # ★ 能力边界硬拦截：干不了的活当场说清，别浪费十几步再骗主人「拆小点」
    refuse = _refuse_if_impossible(content)
    if refuse:
        logger.warning(f"[pc_agent] {tid} 命中能力边界，直接拒绝：{content[:60]}")
        _rewrite_status(path, "failed", refuse, 0, 0)
        _move_to_done(path)
        return {"tid": tid, "ok": False, "result": refuse, "ms": 0, "steps": 0}

    # 先落盘 running —— 进程中途挂掉也不会被重复执行
    _rewrite_status(path, "running")
    logger.info(f"[pc_agent] 开始执行 {tid}：{content[:80]}")

    # 主人指定了空间/项目就把活落进那个目录（说「在某某项目里…」时生效）
    ws = _resolve_workspace(_read_workspace(path))
    task_for_agent = content
    if ws:
        task_for_agent = (
            f"【工作空间】{ws}\n"
            f"这项任务要在上面这个目录里完成。文件读写、脚本执行都请以它为基准目录。\n\n"
            f"{content}"
        )
        logger.info(f"[pc_agent] {tid} 指定工作空间 -> {ws}")

    try:
        out = run_agent(task_for_agent)
    except Exception as e:
        logger.error(f"[pc_agent] 执行异常: {type(e).__name__}: {e}")
        out = {"ok": False, "result": "执行时出了岔子（%s）。" % type(e).__name__, "ms": 0, "steps": 0}

    result = (out.get("result") or "").strip() or "任务处理完了，但没拿到结果。"
    # ★ 把 steps / ms 一起落盘（run_agent 早就算好了，以前白白丢掉）
    _rewrite_status(path, "done" if out.get("ok") else "failed", result,
                    out.get("steps"), out.get("ms"))
    _move_to_done(path)

    # 2026-10-03：动过文件后让资源管理器刷新。
    # 主人实测删完截图，文件没了但窗口还显示着，得手动 F5 —— 看着像没删成功。
    # 这里兜底刷一次（没开文件窗口也不报错），省得每类操作各写一遍。
    try:
        from core.pc_desktop import refresh_desktop

        r = refresh_desktop()
        if r.get("refreshed"):
            logger.info(f"[pc_agent] {tid} 已刷新资源管理器：{r.get('msg')}")
    except Exception as e:
        logger.debug(f"[pc_agent] 刷新桌面跳过: {e}")

    logger.info(
        f"[pc_agent] {tid} 完成（{out.get('ms')}ms，{out.get('steps')}步）：{result[:100]}"
    )
    return {"tid": tid, "ok": bool(out.get("ok")), "result": result, "ms": out.get("ms", 0)}


def _move_to_done(path: Path):
    try:
        inbox, done, _ = _dirs()
        target = done / path.name
        if target.exists():
            target = done / ("%s-%d.md" % (path.stem, int(time.time())))
        path.replace(target)
    except Exception as e:
        logger.error(f"[pc_agent] 移动到 done 失败: {type(e).__name__}: {e}")


def _dirs():
    base = _bridge_dir()
    inbox = base / "inbox"
    done = base / "done"
    announced = base / "announced"
    for d in (inbox, done, announced):
        d.mkdir(parents=True, exist_ok=True)
    return inbox, done, announced


# ---------------------------------------------------------------- 自测入口
if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = "在桌面新建一个 txt 文件，文件名 测试.txt，内容写：pc_agent 自测通过"
    print("=== 直接跑一条任务 ===")
    print("任务：", task)
    r = run_agent(task)
    print("ok =", r["ok"], "| 步数 =", r["steps"], "| 耗时 =", r["ms"], "ms")
    print("结果：", r["result"])
