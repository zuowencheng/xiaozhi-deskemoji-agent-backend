"""
小智本机 MCP Server（自研，stdio 通道）

★ 为什么自己写而不用 npm 的：
  1. 本机 npm 安装被安全策略拦（reg.exe 黑名单），第三方包装不上
  2. npx 每次启动都要联网校验版本，本机到 npm registry 很慢 → 服务端初始化只给 10 秒，必超时
  3. 自研 = 可控、可审计、离线可用，而且它是**我们自己的资产**（标品的一部分）

★ stdio 模式铁律：**任何日志都不能打到 stdout**，那会污染 JSON-RPC 协议流导致握手失败。
  本文件所有诊断信息一律走 LOG（写文件）。调试时看 runtime/logs/local_mcp.log

挂载方式（server/xiaozhi-server/data/.mcp_server_settings.json）：
  "xiaozhi-local": {
    "command": "<venv python.exe>",
    "args": ["<本文件绝对路径>"]
  }

自测：
  python bridge/tools/local_mcp.py --selftest     # 离线测工具逻辑，不启服务
"""
import os
import sys
import json
import time
import fnmatch
import subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]  # xiaozhi-deskemoji-agent/
LOG_DIR = ROOT / "runtime" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "local_mcp.log"

# 目录白名单：只允许读写这些地方（安全边界，改这里要谨慎）
ALLOWED_ROOTS = [
    Path.home() / "Desktop",
    Path.home() / "Documents",
    ROOT,
]
# 额外白名单（可从环境变量 XIAOZHI_MCP_EXTRA_DIRS 追加，分号分隔）
_extra = os.environ.get("XIAOZHI_MCP_EXTRA_DIRS", "")
if _extra:
    ALLOWED_ROOTS += [Path(p).expanduser().resolve() for p in _extra.split(";") if p.strip()]

MAX_OUTPUT = 8000          # 单次返回最大字符数
CMD_TIMEOUT = 30           # 命令超时（秒）
CMD_BLACKLIST = [          # 命令黑名单（命中即拒），运维机器上的保险丝
    "format ", "del ", "del/", "rm -rf", "rmdir /s", "rd /s",
    "shutdown", "taskkill", "reg delete", "reg add", "diskpart",
    "bcdedit", "net user", "takeown", "icacls", "cipher /w",
    ":> ", "mklink", "schtasks /delete", "sc delete",
]


def log(msg: str) -> None:
    """★ 只写文件，绝不 print（stdout 是协议通道）"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%H:%M:%S}] {msg}\n")
    except Exception:
        pass


def _safe(path: str) -> Path:
    """路径白名单校验 + 防穿越。不合法直接抛异常"""
    p = Path(path).expanduser().resolve()
    for root in ALLOWED_ROOTS:
        try:
            r = root.resolve()
        except Exception:
            continue
        if p == r or r in p.parents:
            return p
    raise PermissionError(f"路径不在白名单内：{p}（允许的顶层目录：{[str(x) for x in ALLOWED_ROOTS]}）")


def _decode(b: bytes) -> str:
    """Windows 命令行输出是 GBK，硬用 utf-8 解会乱码"""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return b.decode(enc)
        except Exception:
            continue
    return b.decode("utf-8", errors="replace")


def _clip(s: str) -> str:
    return s[:MAX_OUTPUT] + f"\n...[已截断，共 {len(s)} 字符]" if len(s) > MAX_OUTPUT else s


# ---------------------------------------------------------------- 工具定义
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("xiaozhi-local")


@mcp.tool()
def list_dir(path: str = "", pattern: str = "*") -> str:
    """列出目录下的文件和子目录。path 留空表示列出所有允许的顶层目录。"""
    try:
        if not path:
            return "\n".join(f"[DIR ] {r}" for r in ALLOWED_ROOTS if r.exists())
        d = _safe(path)
        if not d.is_dir():
            return f"不是目录：{d}"
        items = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        out = []
        for it in items:
            if pattern != "*" and not fnmatch.fnmatch(it.name, pattern):
                continue
            tag = "DIR " if it.is_dir() else "FILE"
            size = "" if it.is_dir() else f"  {it.stat().st_size}B"
            out.append(f"[{tag}] {it.name}{size}")
            if len(out) >= 200:
                out.append("...（超过 200 条，用 pattern 缩小范围）")
                break
        return "\n".join(out) or "（空目录）"
    except Exception as e:
        return f"失败：{type(e).__name__}: {e}"


@mcp.tool()
def read_file(path: str, head_lines: int = 0) -> str:
    """读取文本文件内容。head_lines>0 时只读前 N 行（大文件先探路用）。"""
    try:
        p = _safe(path)
        if not p.is_file():
            return f"不是文件：{p}"
        if p.stat().st_size > 5 * 1024 * 1024:
            return f"文件超过 5MB（{p.stat().st_size}B），请用 head_lines 分段读"
        txt = p.read_text(encoding="utf-8", errors="replace")
        if head_lines > 0:
            txt = "\n".join(txt.splitlines()[:head_lines])
        return _clip(txt)
    except Exception as e:
        return f"失败：{type(e).__name__}: {e}"


@mcp.tool()
def write_file(path: str, content: str, append: bool = False) -> str:
    """写入文本文件。append=true 时追加。父目录不存在会自动创建。"""
    try:
        p = _safe(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
        else:
            p.write_text(content, encoding="utf-8")
        log(f"write_file {p} ({len(content)} chars, append={append})")
        return f"已写入 {p}（{len(content)} 字符）"
    except Exception as e:
        return f"失败：{type(e).__name__}: {e}"


@mcp.tool()
def search_files(keyword: str, root: str = "", max_results: int = 30) -> str:
    """按文件名搜索（不搜内容，内容搜索请用 grep_files）。root 留空=搜所有白名单目录。"""
    try:
        roots = [_safe(root)] if root else [r for r in ALLOWED_ROOTS if r.exists()]
        hits = []
        for r in roots:
            for dirpath, dirnames, filenames in os.walk(r):
                dirnames[:] = [d for d in dirnames if not d.startswith((".", "__", "node_modules"))]
                for fn in filenames:
                    if keyword.lower() in fn.lower():
                        hits.append(str(Path(dirpath) / fn))
                        if len(hits) >= max_results:
                            return "\n".join(hits) + f"\n...（已达上限 {max_results}）"
        return "\n".join(hits) or "没找到"
    except Exception as e:
        return f"失败：{type(e).__name__}: {e}"


@mcp.tool()
def grep_files(keyword: str, root: str = "", pattern: str = "*.md", max_results: int = 20) -> str:
    """在文件内容里搜关键词。适合找"哪份文档里提到了 X"。"""
    try:
        import re
        roots = [_safe(root)] if root else [r for r in ALLOWED_ROOTS if r.exists()]
        hits = []
        rx = re.compile(re.escape(keyword), re.I)
        for r in roots:
            for dirpath, dirnames, filenames in os.walk(r):
                dirnames[:] = [d for d in dirnames if not d.startswith((".", "__", "node_modules"))]
                for fn in filenames:
                    if not fnmatch.fnmatch(fn, pattern):
                        continue
                    fp = Path(dirpath) / fn
                    try:
                        if fp.stat().st_size > 2 * 1024 * 1024:
                            continue
                        txt = fp.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        continue
                    for i, line in enumerate(txt.splitlines(), 1):
                        if rx.search(line):
                            hits.append(f"{fp}:{i}: {line.strip()[:160]}")
                            break
                    if len(hits) >= max_results:
                        return "\n".join(hits) + f"\n...（已达上限 {max_results}）"
        return "\n".join(hits) or "没找到"
    except Exception as e:
        return f"失败：{type(e).__name__}: {e}"


@mcp.tool()
def fetch_url(url: str, max_chars: int = 4000) -> str:
    """抓取网页正文（去掉脚本样式，只留文字）。适合"帮我看看这个链接说了什么"。"""
    try:
        import httpx
        from bs4 import BeautifulSoup
        with httpx.Client(follow_redirects=True, timeout=20,
                          headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = c.get(url)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "noscript"]):
            t.decompose()
        text = "\n".join(x.strip() for x in soup.get_text("\n").splitlines() if x.strip())
        return _clip(text[:max_chars])
    except Exception as e:
        return f"抓取失败：{type(e).__name__}: {e}"


@mcp.tool()
def run_shell(command: str, timeout: int = CMD_TIMEOUT) -> str:
    """执行一条本机命令并返回输出（PowerShell / cmd 语法都可以）。
    只适合单条简单命令；多步复杂任务请用 pc_run_task。
    危险命令（删除/格式化/杀进程等）会被拦截。"""
    low = command.lower()
    for bad in CMD_BLACKLIST:
        if bad in low:
            return f"已拒绝：命令包含危险关键字（{bad.strip()}）。如需执行请让主人手动跑。"
    try:
        t0 = time.time()
        p = subprocess.run(command, shell=True, capture_output=True,
                           timeout=min(timeout, 120), cwd=str(Path.home()))
        out = _decode(p.stdout) + ("\n[stderr]\n" + _decode(p.stderr) if p.stderr else "")
        ms = int((time.time() - t0) * 1000)
        log(f"run_shell: {command[:120]} -> rc={p.returncode} {ms}ms")
        return f"[退出码 {p.returncode} / {ms}ms]\n{_clip(out.strip()) or '（无输出）'}"
    except subprocess.TimeoutExpired:
        return f"超时（{timeout}s）。命令还在后台跑，别重复执行。"
    except Exception as e:
        return f"失败：{type(e).__name__}: {e}"


@mcp.tool()
def now() -> str:
    """获取当前日期时间、星期、时区。"""
    d = datetime.now()
    wk = "一二三四五六日"[d.weekday()]
    return f"{d:%Y-%m-%d %H:%M:%S} 星期{wk}（本地时间）"


# Everything 秒搜：只有装了 es.exe 才注册这个工具（优雅降级）
def _find_es() -> str | None:
    for c in [r"C:\Program Files\Everything\es.exe",
              r"C:\Program Files (x86)\Everything\es.exe",
              r"C:\Software\Everything\es.exe"]:
        if os.path.exists(c):
            return c
    return None


if _find_es():
    @mcp.tool()
    def everything_search(query: str, max_results: int = 20) -> str:
        """用 Everything 全盘秒搜文件名（比 search_files 快得多，但只搜文件名不搜内容）。"""
        try:
            p = subprocess.run([_find_es(), query, "-n", str(max_results)],
                               capture_output=True, timeout=15)
            return _clip(_decode(p.stdout).strip()) or "没找到"
        except Exception as e:
            return f"失败：{type(e).__name__}: {e}"


# ---------------------------------------------------------------- 自测
def _selftest() -> None:
    """离线验证工具逻辑，不起服务。装完/改完必跑。"""
    print("=== local_mcp 自检 ===")
    print("白名单目录：")
    for r in ALLOWED_ROOTS:
        print(f"  - {r}  {'[存在]' if r.exists() else '[不存在!]'}")

    print("\n[1] now()")
    print("   ", now())

    print("\n[2] list_dir('') 允许的顶层目录")
    print("   ", list_dir("").replace("\n", "\n    "))

    d = Path.home() / "Desktop"
    print(f"\n[3] list_dir({d})")
    r = list_dir(str(d))
    print("   ", r[:300].replace("\n", "\n    "))

    print("\n[4] 安全：读白名单外的路径应被拒")
    r = read_file(r"C:\Windows\System32\config\SAM")
    print("   ", r[:160])
    assert "不在白名单" in r or "失败" in r, "★ 白名单失效！"
    print("    ✅ 已拦截")

    print("\n[5] 安全：路径穿越 ..\\ 应被拒")
    r = read_file(str(d / ".." / ".." / ".." / "Windows" / "win.ini"))
    print("   ", r[:160])
    assert "不在白名单" in r or "失败" in r, "★ 穿越防护失效！"
    print("    ✅ 已拦截")

    print("\n[6] 安全：危险命令应被拒")
    for bad in ["del C:\\x", "format C:", "taskkill /f /im python.exe"]:
        r = run_shell(bad)
        assert "已拒绝" in r, f"★ 危险命令没拦住: {bad}"
        print(f"    ✅ {bad} -> {r[:60]}")

    print("\n[7] run_shell 正常命令")
    print("   ", run_shell("echo xiaozhi-ok")[:200])

    print("\n[8] write_file + read_file 往返")
    tmp = d / "_xiaozhi_mcp_selftest.txt"
    print("   ", write_file(str(tmp), "峰哥测试 123"))
    print("   ", read_file(str(tmp)))
    try:
        tmp.unlink()
        print("    ✅ 临时文件已清理")
    except Exception as e:
        print("    ⚠ 清理失败：", e)

    print("\n自查表：")
    print("  - now / list_dir / read_file / write_file / search_files / grep_files")
    print("  - fetch_url / run_shell / now" + (" / everything_search" if _find_es() else "（Everything 未装，everything_search 未注册）"))
    print("\n自检通过。")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        log(f"local_mcp 启动 python={sys.executable}")
        mcp.run()
