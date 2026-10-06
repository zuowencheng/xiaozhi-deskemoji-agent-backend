"""小智自建后台 · 轻量管理台（纯标准库，无第三方依赖）

为什么自己写：
  上游 Python 版服务端只暴露 OTA(8003) 与视觉解释两个 HTTP 接口，
  官方「智控台」(manager-api + manager-web) 是 Java + MySQL + Redis 的独立系统，
  本机没有 Docker、不值得为几个开关部署一套治理平台。

这个管理台干什么：
  1. 看状态：服务端口、设备在线、投递箱任务、日志尾巴
  2. 改配置：把常用开关（设备常连、唤醒词回复、LLM 选型…）做成表单，写回 .config.yaml
  3. 管服务：一键拉起 / 停止 xiaozhi-server
     —— 关键：本进程由「你」启动，因此它拉起的子进程不会被沙箱回收，
        这也是"后台跑"的正解（AI 会话里拉起的进程会被整棵回收）。

端口：8005（8000=WS / 8003=OTA+视觉 / 8004=主动播报 / 8005=本管理台 / 8006=预留本机桥）

用法：
    python admin_server.py            # 默认 8005
    python admin_server.py 8010       # 自定义端口
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ---------------- 路径与环境 ----------------
ADMIN_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ADMIN_DIR.parent.parent            # xiaozhi-deskemoji-agent/
SERVER_DIR = PROJECT_ROOT / "server" / "xiaozhi-server"
CONFIG_FILE = SERVER_DIR / "data" / ".config.yaml"
UPSTREAM_CONFIG = SERVER_DIR / "config.yaml"      # 上游默认值，页面兜底显示用
# ⚠️ 2026-10-03 修正：原来指向项目根的 _server_run.log —— 那是**通过管理台
#    启动服务时**才写的日志。现在服务是独立常驻的（tools/start_services.py），
#    真正在写的是 xiaozhi-server/tmp/server.log，所以页面显示的一直是陈旧内容。
#    两个都留着：优先读新的，新的没有再回落旧的。
_LIVE_LOG = PROJECT_ROOT / "server" / "xiaozhi-server" / "tmp" / "server.log"
_LIVE_LOG_ERR = PROJECT_ROOT / "server" / "xiaozhi-server" / "tmp" / "server.err.log"
SERVER_LOG = _LIVE_LOG if _LIVE_LOG.exists() else (PROJECT_ROOT / "_server_run.log")
SERVER_LOG_ERR = _LIVE_LOG_ERR if _LIVE_LOG_ERR.exists() else (PROJECT_ROOT / "_server_run.err.log")

# 2026-10-03 主人要求「内容特别多的话加滚动条，不要显示成一长串」——
# 状态接口只带这么多行，页面用 max-height + overflow 滚动，行数可前端切换。
LOG_LINES = 300
BRIDGE_DIR = PROJECT_ROOT / "bridge"
PID_FILE = ADMIN_DIR / "_server.pid"

PYTHON_EXE = r"C:\Users\zuowe\.workbuddy\binaries\python\envs\xiaozhi-server\Scripts\python.exe"
FFMPEG_BIN = r"C:\Users\zuowe\.workbuddy\binaries\ffmpeg\ffmpeg-master-latest-win64-gpl\bin"

PORTS = {8000: "WebSocket 主链路", 8003: "OTA / 视觉", 8004: "主动播报", 11434: "Ollama（已弃用，可不开）"}

# ---------------- 可编辑配置白名单 ----------------
# 只暴露"改了有意义且安全"的项；每一项 = (键名, 所在段, 类型, 说明)
EDITABLE = [
    ("close_connection_no_voice_time", None, "int",
     "无语音多久断开（秒）。120=默认；86400=等效常连，让主动播报随时可用"),
    ("enable_greeting", None, "bool", "唤醒后是否自动说一句问候语（例：我在呢）"),
    ("enable_websocket_ping", None, "bool", "WebSocket 心跳保活，配合长连接防 NAT 静默回收"),
    ("enable_wakeup_words_response_cache", None, "bool", "是否把「你好小智」这类唤醒词的固定回复提前缓存好，命中时立刻回答、不等模型思考（更快更省 token）"),
    ("enable_stop_tts_notify", None, "bool", "旧方案：由服务器发一段音频文件当提示音。功能与下一项重复，一般保持关闭即可"),
    ("tool_call_timeout", None, "int", "工具调用超时（秒）"),
    ("tts_timeout", None, "int", "TTS 合成超时（秒）"),
    ("LLM", "selected_module", "enum", "大脑：DeepSeekLLM(云端,当前) / OllamaLLM(本地,慢) / DoubaoLLM"),
    ("ASR", "selected_module", "enum", "耳朵：SherpaASR(本地,不出网) / DoubaoASR(云端)"),
    ("TTS", "selected_module", "enum", "嘴：EdgeTTS(免费) / Doubao / Aliyun / Tencent"),
    ("Memory", "selected_module", "enum", "记忆：nomem(关) / mem_local_short(本地短记忆) / mem0ai / powermem"),
    # 2026-10-03 主人要"xiaozhi.me 后台那个语速滑块"搬到本地：
    #   这两个是 EdgeTTS 段的参数，改完需重启后台生效。
    ("rate", "TTS", "int", "语速%（仅 EdgeTTS 生效，-100~100）。当前用火山请调下一项"),
    ("play_sound_after_reply", "runtime", "bool", "是否在它说完话后，由设备本地播一声轻柔提示音，告诉你「可以开始说了」。关掉则完全静音"),
    ("speed_ratio", "TTS", "float", "★语速倍率（火山生效，1.0=原速）：1.2=快20%，0.8=慢20%"),
    ("pitch", "TTS", "int", "音调 Hz（-100~100）：0=默认，正数更尖"),
    # 2026-10-03 主人反馈"说话慢仍被切断"——服务端这层也要能远程调，不用刷机
    ("min_silence_duration_ms", "VAD", "int", "★ 静音多久算说完(ms)：默认1000，说话慢就调大(1500更稳)"),
    ("threshold_low", "VAD", "float", "★ 静音判定下限(0~1，现0.3)：调小=更难判静音=更宽容"),
    # 2026-10-04 主人要「几点之后别再主动通知」。
    #   只拦主动播报（任务完成/提醒/额度告警），对话回复不受影响。
    #   静默期内的消息入队不丢，天亮后补播。
    ("quiet_hours_enabled", "notify", "bool",
     "★ 免打扰：开启后，下面这段时间里小智不主动出声（你问它答照样回答）"),
    ("quiet_hours_start", "notify", "time", "免打扰起点，格式 时:分（如 23:00）"),
    ("quiet_hours_end", "notify", "time", "免打扰终点（如 07:00）。起点晚于终点 = 跨过零点"),
    # 2026-10-04 事故：待办 due=今天15:00 被 12 小时预告窗口在**凌晨 3 点**念出来。
    #   免打扰默认关兜不住，所以待办巡检单独一道时段闸（默认开）。
    ("todo_speak_start", "notify", "time",
     "★ 待办提醒可播起点（默认 07:00）：这段窗口外不念待办，避免半夜被叫醒"),
    ("todo_speak_end", "notify", "time",
     "待办提醒可播终点（默认 22:00）。起止填一样 = 全天可播"),
    # 2026-10-04 主人：「一直处于聆听中」——说完话后多久没声音就回待机。
    #   握手时下发给设备，改完重启后台即生效，不用刷机。
    ("listening_timeout_sec", None, "int",
     "★ 聆听超时（秒）：说完话后多久没声音就回待机。0=一直聆听（老行为）"),
]
def llm_choices() -> list:
    """LLM 下拉选项 = 现有模块 + data/.config.yaml 的 llm_profiles 并集。

    2026-10-03 主人要求「支持多模型、也支持多个模型切换」。
    原来这里是硬编码 4 个选项，加新模型要改代码；改成动态读配置后，
    往 llm_profiles 里加一段就自动出现在下拉里。
    """
    base = ["DeepSeekLLM", "OllamaLLM", "OllamaOpenAI", "DoubaoLLM"]
    try:
        import yaml
        cfg = yaml.safe_load(read_config_text()) or {}
        prof = cfg.get("llm_profiles") or {}
    except Exception:
        prof = {}
    for k, v in prof.items():
        if k not in base:
            base.append(k)
    return base


def llm_profiles_info() -> dict:
    """给页面用：每个档案的 label / note / 是否当前在用 / 是否缺 key。"""
    try:
        import yaml
        cfg = yaml.safe_load(read_config_text()) or {}
    except Exception:
        return {}
    prof = cfg.get("llm_profiles") or {}
    cur = ((cfg.get("selected_module") or {}).get("LLM")) or ""
    out = {}
    for k, v in prof.items():
        key = (v.get("api_key") or "").strip() if isinstance(v, dict) else ""
        missing = bool(v.get("need_key")) and (not key or key.endswith("-xxx")
                                               or "填你的" in key)
        out[k] = {"label": v.get("label") or k, "note": v.get("note") or "",
                  "model": v.get("model_name") or "",
                  "current": (k == cur), "key_missing": missing}
    return out


def search_engines_info() -> dict:
    """搜索引擎链状态 + 各引擎额度。"""
    info = {"engines": {}, "order": [], "quota": []}
    try:
        import yaml
        cfg = yaml.safe_load(read_config_text()) or {}
        ws = (cfg.get("plugins") or {}).get("web_search") or {}
        info["order"] = ws.get("order") or []
        for k, v in (ws.get("engines") or {}).items():
            info["engines"][k] = {
                "enabled": bool(v.get("enabled")),
                "has_key": bool((v.get("api_key") or "").strip()),
                "note": v.get("note") or "",
            }
    except Exception:
        pass
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "server" / "xiaozhi-server"))
        from core.providers.tts.web_search_multi import quota_report
        info["quota"] = quota_report()
    except Exception:
        pass
    return info


ENUM_CHOICES = {
    "LLM": llm_choices(),
    "ASR": ["SherpaASR", "DoubaoASRStream", "DoubaoASR"],
    "TTS": ["EdgeTTS", "Doubao", "Aliyun", "Tencent", "Openai"],
    "Memory": ["nomem", "mem_local_short", "mem0ai", "powermem", "mem_report_only"],
}

# 2026-10-03 主人反馈"选项不好筛选、不明显" → 加分组 + 中文标签。
# ⚠️ 不塞进 EDITABLE 元组（那边是 4 元组解包，加字段会炸），独立映射更安全。
GROUPS = [
    ("engine", "引擎", "大脑 / 耳朵 / 嘴 / 记忆，换实现"),
    ("voice", "语音参数", "语速音调，改完重启生效"),
    ("vad", "打断灵敏度", "说话慢就调大静音判定"),
    ("conn", "连接保活", "长连接相关，别乱改"),
    ("ui", "交互", "唤醒问候、缓存等"),
    ("timeout", "超时", "卡住时的最长等待"),
    ("quiet", "免打扰", "深夜别让它突然出声（只拦主动播报）"),
]
GROUP_OF = {
    "LLM": "engine", "ASR": "engine", "TTS": "engine", "Memory": "engine",
    "play_sound_after_reply": "唤醒后是否问候",
    "rate": "voice", "pitch": "voice", "speed_ratio": "voice",
    "min_silence_duration_ms": "vad", "threshold_low": "vad",
    "close_connection_no_voice_time": "conn", "enable_websocket_ping": "conn",
    "enable_greeting": "ui", "enable_wakeup_words_response_cache": "ui",
    "enable_stop_tts_notify": "ui",
    "tool_call_timeout": "timeout", "tts_timeout": "timeout",
    "quiet_hours_enabled": "quiet",
    "quiet_hours_start": "quiet", "quiet_hours_end": "quiet",
    "todo_speak_start": "quiet", "todo_speak_end": "quiet",
    "listening_timeout_sec": "ui",
}
LABELS = {
    "LLM": "大脑（决定小智怎么想、怎么说话）",
    "ASR": "耳朵（把声音转成文字）",
    "TTS": "嘴（把文字转成声音）",
    "Memory": "记忆（server 端对话记忆）",
    "rate": "语速 %",
    "play_sound_after_reply": "说完话后是否播提示音",
    "pitch": "音调 Hz",
    "min_silence_duration_ms": "静音多久算说完（毫秒）",
    "threshold_low": "静音判定下限（0~1）",
    "close_connection_no_voice_time": "无语音多久断开（秒）",
    "enable_websocket_ping": "是否开启心跳保活",
    "enable_greeting": "唤醒后是否回一句问候",
    "enable_wakeup_words_response_cache": "唤醒词回复是否走缓存",
    "enable_stop_tts_notify": "说完话是否由服务端播提示音",
    "tool_call_timeout": "工具调用超时（秒）",
    "tts_timeout": "TTS 合成超时（秒）",
    "quiet_hours_enabled": "免打扰：开启后这段时间不主动出声",
    "quiet_hours_start": "免打扰起点（时:分）",
    "quiet_hours_end": "免打扰终点（时:分）",
    "todo_speak_start": "待办提醒可播起点（时:分）",
    "todo_speak_end": "待办提醒可播终点（时:分）",
    "listening_timeout_sec": "聆听超时（秒）· 改完重启后台",
}
# 下拉项的中文说明，显示在选项右边
CHOICE_NOTES = {
    "DeepSeekLLM": "云端 · 快、便宜、联网",
    "OllamaLLM": "本机离线 · 很慢，已弃用",
    "OllamaOpenAI": "本机 OpenAI 兼容接口",
    "DoubaoLLM": "火山云端 · 需 appid+token",
    "SherpaASR": "本地离线 · 不出网、免费(当前)",
    "DoubaoASRStream": "豆包流式2.0 · 大模型更准,出网,约1元/小时",
    "DoubaoASR": "豆包按次 · 便宜但非流式",
    "DoubaoASR": "豆包流式 · 需开通服务",
    "EdgeTTS": "微软免费在线 · 晓晓(当前)",
    "Doubao": "火山豆包合成",
    "Aliyun": "阿里云合成",
    "Tencent": "腾讯云合成",
    "Openai": "OpenAI 兼容接口",
    "nomem": "关闭 server 记忆",
    "mem_local_short": "本地短期记忆",
    "mem0ai": "mem0ai 接口记忆",
    "powermem": "PowerMem 记忆",
    "mem_report_only": "只生成记忆报告",
}


# ---------------- 工具函数 ----------------
def port_alive(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def pid_on_port(port: int):
    """用 netstat 找监听某端口的 PID（Windows）"""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                             text=True, timeout=8, errors="replace").stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].endswith(":%d" % port):
            return parts[4]
    return None


def read_config_text() -> str:
    return CONFIG_FILE.read_text(encoding="utf-8") if CONFIG_FILE.exists() else ""


def parse_simple(text: str) -> dict:
    """只提取我们关心的几项，避免全量解析（保住 YAML 里的注释）

    注意：取值必须容忍行尾注释（如 `key: 86400   # 说明`），
    否则会读成 None，进而写回时误判为"键不存在"而在文件末尾追加重复键。
    """
    result = {}
    for key, section, _t, _d in EDITABLE:
        body = text if section is None else extract_section(text, section)
        # YAML 行内注释前必须有空白，# 紧跟值（如 86400#x）不算注释
        pat = r"^%s%s:[ \t]*([^#\s][^\n\r]*?)[ \t]*(?:#.*)?$" % (
            "" if section is None else r"[ \t]+", re.escape(key))
        m = re.search(pat, body, re.M)
        if m:
            v = m.group(1).strip()
            # time 类型落盘时带了引号（防 YAML 六十进制），读回来要剥掉，
            # 否则 <input type="time"> 拿到 '"23:00"' 显示不出来。
            if _t == "time":
                v = v.strip('"').strip("'")
            result[key] = v
        else:
            result[key] = None
    return result


def extract_section(text: str, section: str) -> str:
    """取某个顶层段落的文本（从 'section:' 到下一个顶格非空行）"""
    lines = text.splitlines()
    out, inside = [], False
    for line in lines:
        if re.match(r"^%s:\s*$" % re.escape(section), line):
            inside = True
            continue
        if inside:
            if line.strip() and not line.startswith((" ", "\t", "#")):
                break
            out.append(line)
    return "\n".join(out)


def write_config_value(text: str, key: str, section, value: str) -> str:
    """写回配置：优先原地替换已有行，缺失则在段内/文件末尾补一行。

    行尾注释必须保留（那是给人看的说明），所以替换时只动"值"这一截。
    """
    if section is None:
        pat = re.compile(r"^(%s:[ \t]*)([^#\n\r]*?)([ \t]*)(#.*)?$" % re.escape(key), re.M)

        def _repl(m):
            comment = m.group(4) or ""
            return m.group(1) + value + ("  " + comment if comment else "")

        if pat.search(text):
            return pat.sub(_repl, text, count=1)
        return text.rstrip("\n") + "\n\n# 由管理台追加\n%s: %s\n" % (key, value)

    # 段内替换
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^%s:\s*$" % re.escape(section), line):
            start = i
            break
    if start is None:
        return text.rstrip("\n") + "\n\n%s:\n  %s: %s\n" % (section, key, value)
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and not lines[j].startswith((" ", "\t", "#")):
            end = j
            break
    for j in range(start + 1, end):
        # ⚠️ 2026-10-03 血泪：原来这里是 lines[j] = "  %s: %s" % (key, value)，
        #    把行缩进硬写成 2 空格。但 TTS→EdgeTTS→rate 是三层嵌套（rate 本行 4 空格），
        #    压成 2 空格后 rate 跳到了 TTS 层，结构错乱 →
        #    yaml.safe_load 报 "mapping values are not allowed here in output_dir: tmp/"。
        #    ★ 正确做法：保留原行缩进，只换值。
        m_ind = re.match(r"^(\s+)%s:\s*\S" % re.escape(key), lines[j])
        if m_ind:
            lines[j] = "%s%s: %s" % (m_ind.group(1), key, value)
            return "\n".join(lines) + "\n"
    # 段内没有这个键：插到段的第一行下方（缩进 2 空格 = 段的直接子级）
    lines.insert(start + 1, "  %s: %s" % (key, value))
    return "\n".join(lines) + "\n"


# loguru 行格式：2026-10-03 13:33:22 - 0.9.7_xxx - core.providers.asr.base - INFO - 消息
_LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"
    r"\s*-\s*(?P<ver>\S+)"
    r"\s*-\s*(?P<mod>[\w\.]+)"
    r"\s*-\s*(?P<lvl>[A-Z]+)"
    r"(?:\s*-\s*(?:[\w\.]+\s*-\s*)?)?"
    r"(?P<msg>.*)$"
)


def parse_log_lines(lines):
    """把 loguru 行拆成 {ts, time, lvl, mod, msg}，前端一行一条渲染。

    2026-10-03 主人反馈「log 相当于堆叠状态，不容易读取」——
    原来是把整个尾部塞进一个 <pre>，几千字符连成一片。
    拆成字段后：时间一个窄列、级别一个小徽章、模块可折叠、消息占剩余宽度。
    """
    out = []
    for ln in lines:
        m = _LOG_RE.match(ln)
        if m:
            d = m.groupdict()
            out.append({
                "ts": d["ts"],
                "time": d["ts"][11:],          # 只留 HH:MM:SS，省宽度
                "lvl": d["lvl"],
                "mod": d["mod"].split(".")[-1],  # 只留最后一段（asr.base）
                "msg": d["msg"].strip(),
            })
        else:
            # 续行（如 traceback 的第二行）——挂到上一条下面
            if out and ln.startswith((" ", "\t")):
                out[-1]["msg"] += "\n" + ln.rstrip()
            elif out:
                out[-1]["msg"] += " " + ln.strip()
            else:
                out.append({"ts": "", "time": "", "lvl": "INFO",
                            "mod": "", "msg": ln.strip()})
    return out


def tail(path, n=30, parse=False):
    """读日志尾部，剥掉 loguru 的 ANSI 颜色码。

    2026-10-03 主人反馈页面底部全是乱码（形如 [0m[0m,9.7_xxx[0m）：
    loguru 会往日志里写 ESC[...m 颜色序列，直接塞进 HTML 就是一堆控制字符。
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            raw = f.read().decode("utf-8", "replace")
    except Exception:
        return []
    ans = []
    for ln in raw.replace("\r\n", "\n").split("\n")[-n:]:
        ln = re.sub("\x1b\[[0-9;]*m", "", ln)   # 完整颜色序列
        # 只处理完整 ANSI 序列（ESC + [ + 数字/分号 + m）。
        # ⚠️ 千万别再加"没 ESC 前缀也删"的兜底正则 —— 它会把
        #    正常文本里的 "2026-10-03" 这类内容误伤成 "261002"。
        ln = ln.replace("\x1b", "")
        if ln.strip():
            ans.append(ln)
    return parse_log_lines(ans) if parse else ans


def list_tasks():
    def scan(sub):
        d = BRIDGE_DIR / sub
        if not d.exists():
            return []
        items = []
        for f in sorted(d.glob("task-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            head = f.read_text(encoding="utf-8", errors="replace")[:1500]
            title = ""
            m = re.search(r"^#\s*(.+)$", head, re.M)
            if m:
                title = m.group(1).strip()
            body = ""
            m2 = re.search(r"##\s*任务内容\s*\n+(.+?)(?:\n##|\Z)", head, re.S)
            if m2:
                body = m2.group(1).strip()[:200]
            items.append({"name": f.name, "title": title, "body": body,
                          "mtime": time.strftime("%m-%d %H:%M", time.localtime(f.stat().st_mtime))})
        return items
    return {"inbox": scan("inbox"), "done": scan("done")}


def llm_usage_report():
    """LLM token 用量（bridge/llm_usage.json）。

    为什么自己做账：阿里云/DeepSeek 都**不提供额度查询 API**，
    只有控制台能看。所以每次响应里返回的 usage.total_tokens 落到本地账本。
    """
    try:
        sys.path.insert(0, str(PROJECT_ROOT / "bridge"))
        import llm_usage
        rep = llm_usage.report(7)
        return {"ok": True, "today": llm_usage.today(), "days": rep["by_day"],
                "total_tokens": rep["total_tokens"],
                "total_calls": rep["total_calls"],
                "alerts": llm_usage.alerts()}
    except Exception as e:
        return {"ok": False, "message": str(e)[:120], "today": {}, "alerts": []}


def workdir_report():
    """桌面「小智助手过程文档」目录的占用情况 + 清理入口。

    2026-10-03 主人要求「桌面上专门建一个目录叫小智助手过程文档，
    以后他交互时生成的临时资料都扔这里，到时候定期清理或者让他清理」。
    """
    import subprocess
    code = (
        "import sys,json;sys.path.insert(0,r'%s');"
        "from core.workdir import stats;print(json.dumps(stats(),ensure_ascii=False))"
        % str(PROJECT_ROOT / "server" / "xiaozhi-server").replace("\\", "\\\\")
    )
    try:
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=20)
        d = json.loads(out.stdout.decode("utf-8", "replace").strip() or "{}")
        d["ok"] = bool(d.get("exists"))
        return d
    except Exception as e:
        return {"ok": False, "message": str(e)[:120]}


def selftest_report():
    """读最近一次回归测试报告（tools/selftest.py 产出）。

    2026-10-03 主人要求「不要出现我们认为保留了、实际上不可用」。
    解决办法就是**每次都真跑一遍**并把结果摆到管理台上，不用等用户撞上。
    """
    f = PROJECT_ROOT / "docs" / "selftest-report.json"
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "message": "还没有跑过回归测试"}
    items = d.get("items", [])
    return {"ok": True, "ts": d.get("ts", ""),
            "pass": d.get("pass", 0), "fail": d.get("fail", 0),
            "skip": d.get("skip", 0),
            "fails": [i for i in items if i.get("status") == "FAIL"],
            "items": items}


def device_status():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8004/notify/status", timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}


def effective_config() -> dict:
    """页面显示"实际生效值"：私有配置优先，缺的回落上游 config.yaml 默认。

    上游读取顺序就是 data/.config.yaml 覆盖 config.yaml，
    所以这里做同样的回落，避免页面上一堆空值让人误判成"没配置"。
    """
    vals = parse_simple(read_config_text())
    missing = [k for k, v in vals.items() if v is None]
    if missing and UPSTREAM_CONFIG.exists():
        fb = parse_simple(UPSTREAM_CONFIG.read_text(encoding="utf-8", errors="replace"))
        for k in missing:
            vals[k] = fb.get(k)
    return vals


def collect_state():
    ports = {str(p): {"desc": d, "alive": port_alive(p), "pid": pid_on_port(p) if port_alive(p) else None}
             for p, d in PORTS.items()}
    # 端口改成页面好渲染的数组结构
    ports_arr = [{"port": p, "name": v["desc"], "ok": v["alive"]} for p, v in ports.items()]
    st = {
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ports": ports_arr,
        "config": effective_config(),
        "device": device_status(),
        "tasks": list_tasks(),
        # 2026-10-03 主人要求「每行一条 + 滚动条，不要堆成一长串」：
        # 后端就解析成结构化字段，前端一行一条渲染，页面上限高 + overflow。
        "selftest": selftest_report(),
        "workdir": workdir_report(),
        "llm_usage": llm_usage_report(),
        "llm_profiles": llm_profiles_info(),
        "search_engines": search_engines_info(),
        "log": tail(SERVER_LOG, LOG_LINES, parse=True),
        "log_err": tail(SERVER_LOG_ERR, 12, parse=True),
    }
    # ---- 2026-10-03 管理台重构：补页面要用的汇总字段 ----
    dev = st.get("device") or {}
    st["online"] = dev.get("online") or []
    # /notify/status 里没有 ip 字段，从本机网卡推断（后台与设备同一局域网）
    if not st.get("device_ip"):
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("223.5.5.5", 80))
            st["device_ip"] = s.getsockname()[0]
            s.close()
        except Exception:
            st["device_ip"] = "127.0.0.1"
    st["version"] = dev.get("version") or ""
    st["memory_stats"] = _mem_stats()
    st["todo"] = _todo_stats()
    st["quota"] = _quota_stats()
    return st


def _load_bridge_mod(name):
    import importlib.util
    for up in (2, 3, 4):
        p = (Path(__file__).resolve().parents[up]) / "bridge"
        if p.exists():
            f = p / name
            if f.exists():
                spec = importlib.util.spec_from_file_location("xz_adm_" + name[:-3], str(f))
                m = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(m)
                return m
    return None


def _mem_stats():
    """记忆条数统计。

    ⚠️ 2026-10-03 踩过的坑：这里原来调 `memory.recent(500)`，而 recent() 内部要算
       向量相似度 → **每次刷新页面都会打 Ollama**，一旦 Ollama 慢/未起就整页卡住。
    现在改成**纯 SQL COUNT**，毫秒返回，不碰任何模型。
    """
    m = _load_bridge_mod("memory.py")
    if m is None:
        return {}
    try:
        c = m.connect()
        rows = c.execute("SELECT kind, COUNT(*) n FROM memories GROUP BY kind").fetchall()
        c.close()
        by = {k: 0 for k in ("chat", "profile", "project", "knowledge", "task")}
        for r in rows:
            by[r[0]] = r[1]
        return {"total": sum(by.values()), "by_kind": by}
    except Exception:
        return {}


def _todo_stats():
    t = _load_bridge_mod("todo.py")
    if t is None:
        return {}
    try:
        return t.stats()
    except Exception:
        return {}


def _quota_stats():
    q = _load_bridge_mod("quota.py")
    if q is None:
        return {}
    try:
        return {c["channel"]: {"remaining": c["remaining"], "quota": c["quota"]}
                for c in q.stats()}
    except Exception:
        return {}


# ---------------- 服务进程管理 ----------------
_server_proc = {"p": None}


def server_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PATH"] = FFMPEG_BIN + os.pathsep + env.get("PATH", "")
    env.pop("MSYSTEM", None)
    env.pop("MINGW_PREFIX", None)
    return env


def start_server():
    if port_alive(8000):
        return False, "8000 端口已在监听，服务本来就活着"
    py = PYTHON_EXE if Path(PYTHON_EXE).exists() else sys.executable
    fout = open(SERVER_LOG, "a", encoding="utf-8", errors="replace")
    ferr = open(SERVER_LOG_ERR, "a", encoding="utf-8", errors="replace")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    p = subprocess.Popen([py, "app.py"], cwd=str(SERVER_DIR), env=server_env(),
                         stdout=fout, stderr=ferr, creationflags=flags)
    _server_proc["p"] = p
    PID_FILE.write_text(str(p.pid), encoding="utf-8")
    return True, "已拉起服务，PID=%d，约 15 秒后可用" % p.pid


def stop_server():
    pid = pid_on_port(8000)
    if not pid:
        return False, "8000 端口没在监听，服务本来就没跑"
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=10)
        return True, "已停止 PID=%s" % pid
    except Exception as e:
        return False, "停止失败: %s" % e


# ---------------- HTTP ----------------
# 2026-10-03：主页面与「记忆库」独立页共用同一套 CSS（改一处两页同步生效）
SHARED_CSS = r"""
:root{
  --bg:#0f1115; --panel:#171a21; --panel2:#1d212a; --line:#262b36;
  --fg:#e6e9ef; --mut:#8b93a3; --dim:#5d6472;
  --acc:#4a9eff; --ok:#3ecf8e; --warn:#f5a623; --err:#f2555a;
  --r:10px;
}
@media (prefers-color-scheme: light){
  :root{--bg:#f4f6f9;--panel:#fff;--panel2:#f8fafc;--line:#e3e8ef;
        --fg:#1a1f29;--mut:#5d6675;--dim:#8b93a3;}
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.6 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
a{color:var(--acc);text-decoration:none}
code,.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:12px}

/* ---------- 顶栏 ---------- */
header{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--panel) 92%,transparent);
  backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
.hdr{max-width:1400px;margin:0 auto;padding:12px 22px;display:flex;align-items:center;gap:18px}
.brand{font-size:16px;font-weight:600;letter-spacing:.3px;white-space:nowrap}
.brand small{color:var(--dim);font-weight:400;margin-left:8px;font-size:12px}
.nav{display:flex;gap:2px;flex:1;overflow-x:auto}
.nav a{color:var(--mut);padding:5px 11px;border-radius:7px;font-size:13px;white-space:nowrap}
.nav a:hover{background:var(--panel2);color:var(--fg)}
.hdr-r{display:flex;align-items:center;gap:12px;white-space:nowrap}
.dot{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--mut)}
.dot i{width:8px;height:8px;border-radius:50%;background:var(--dim);display:block}
.dot.on i{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.dot.off i{background:var(--err)}

/* ---------- 布局 ---------- */
.wrap{max-width:1400px;margin:0 auto;padding:20px 22px 60px}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}
.metric{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:13px 15px}
.metric .k{font-size:12px;color:var(--mut)}
.metric .v{font-size:22px;font-weight:600;margin-top:3px;letter-spacing:-.3px}
.metric .s{font-size:11px;color:var(--dim);margin-top:1px}
.cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px;align-items:start}
@media(max-width:1000px){.cols{grid-template-columns:1fr}}
section{scroll-margin-top:70px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
  margin-bottom:18px;overflow:hidden}
.card>h2{font-size:14px;font-weight:600;margin:0;padding:13px 16px;border-bottom:1px solid var(--line);
  display:flex;align-items:center;gap:9px}
.card>h2 .hint{font-weight:400}
.card .bd{padding:14px 16px}
.hint{color:var(--mut);font-size:12px}

/* ---------- 控件 ---------- */
button{font:inherit;font-size:13px;padding:6px 13px;border-radius:7px;cursor:pointer;
  border:1px solid var(--line);background:var(--panel2);color:var(--fg);transition:.12s}
button:hover{border-color:var(--acc);color:var(--acc)}
button.pri{background:var(--acc);border-color:var(--acc);color:#fff}
button.pri:hover{filter:brightness(1.1);color:#fff}
button.mini{font-size:11px;padding:2px 9px}
button.dgr:hover{border-color:var(--err);color:var(--err)}
input,select,textarea{font:inherit;font-size:13px;padding:6px 10px;border-radius:7px;
  border:1px solid var(--line);background:var(--panel2);color:var(--fg);width:100%}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--acc)}
label.chk{display:flex;align-items:center;gap:7px;cursor:pointer;font-size:13px}
input[type=checkbox]{width:auto;accent-color:var(--acc)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{cursor:pointer}
.chip.act{background:var(--acc);border-color:var(--acc);color:#fff}
.tag{font-size:11px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);
  color:var(--mut);white-space:nowrap}
.tag.k-profile{border-color:#7f77dd;color:#a49cf0}
.tag.k-knowledge{border-color:#1d9e75;color:#4fc79c}
.tag.k-project{border-color:#378add;color:#7cb7f0}
.tag.k-task{border-color:#ba7517;color:#e0a94f}
.tag.k-chat{border-color:#888780;color:#b4b2a9}

/* ---------- 配置项 ---------- */
details.grp{border:1px solid var(--line);border-radius:9px;margin-bottom:8px;background:var(--panel2)}
details.grp>summary{cursor:pointer;padding:9px 13px;font-size:13px;font-weight:500;list-style:none;
  display:flex;align-items:center;gap:8px}
details.grp>summary::-webkit-details-marker{display:none}
details.grp>summary:before{content:"▸";color:var(--dim);font-size:11px}
details.grp[open]>summary:before{content:"▾"}
details.grp>summary:hover{color:var(--acc)}
.grp-bd{padding:4px 13px 12px;border-top:1px solid var(--line)}
.row{display:grid;grid-template-columns:1fr 190px;gap:12px;align-items:center;
  padding:7px 0;border-bottom:1px dashed var(--line)}
.row:last-child{border-bottom:none}
.row .lb{font-size:13px}
.row .lb small{display:block;color:var(--dim);font-size:11px;margin-top:1px;line-height:1.45}
.row .ctl{display:flex;gap:6px;align-items:center;justify-content:flex-end}

/* ---------- 列表 ---------- */
.mem{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:8px;background:var(--panel2)}
.memh{display:flex;align-items:center;gap:8px;margin-bottom:5px;flex-wrap:wrap}
.memc{font-size:13px;white-space:pre-wrap;word-break:break-word}
pre.log{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:11px;
  max-height:280px;overflow:auto;font-size:11.5px;line-height:1.65;margin:0;white-space:pre-wrap}

/* ---------- 2026-10-03 日志：一行一条 + 固定高度滚动 ----------
   主人原话「log 相当于堆叠状态，不容易读取，做成每行一条，内容多就加滚动条」。
   实现：不用 <pre>（它是整块纯文本），改成表格化 div，每行 flex 布局：
   时间(窄) | 级别(徽章) | 模块(可省略) | 消息(占满，可换行)
   容器 max-height 固定 + overflow-y:auto，超出就滚动，不撑爆页面。 */
.logbar{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
.logbar input{flex:1;min-width:180px}
.logbar select{width:120px}
.chk{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--mut);white-space:nowrap}
.chk input{width:auto}
.logbox{background:var(--panel2);border:1px solid var(--line);border-radius:8px;
  max-height:420px;overflow-y:auto;overflow-x:hidden;font-size:12px;line-height:1.55}
.logbox::-webkit-scrollbar,.scroll-y::-webkit-scrollbar{width:9px;height:9px}
.logbox::-webkit-scrollbar-thumb,.scroll-y::-webkit-scrollbar-thumb{
  background:var(--line);border-radius:9px}
.logbox::-webkit-scrollbar-thumb:hover,.scroll-y::-webkit-scrollbar-thumb:hover{
  background:var(--dim)}
.logbox::-webkit-scrollbar-track{background:transparent}
.lg{display:flex;gap:9px;padding:4px 10px;border-bottom:1px solid var(--line);
  align-items:flex-start}
.lg:last-child{border-bottom:none}
.lg:hover{background:var(--panel2)}
.lg-t{font-family:var(--mono);color:var(--dim);flex:0 0 60px;font-size:11px;
  padding-top:1px;white-space:nowrap}
.lg-l{flex:0 0 52px;font-size:10px;font-weight:700;letter-spacing:.4px;
  border-radius:4px;padding:1px 0;text-align:center}
.lg-l.DEBUG{color:var(--dim);background:var(--panel2)}
.lg-l.INFO{color:var(--acc);background:rgba(56,138,221,.14)}
.lg-l.WARNING{color:var(--warn);background:rgba(186,117,23,.16)}
.lg-l.ERROR,.lg-l.CRITICAL{color:var(--err);background:rgba(197,63,63,.16)}
.lg-m{font-family:var(--mono);color:var(--mut);flex:0 0 96px;font-size:11px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding-top:1px}
.lg-x{flex:1;min-width:0;font-family:var(--mono);font-size:11.5px;
  white-space:pre-wrap;word-break:break-word;color:var(--fg)}
.logbox.nowrap .lg-x{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.logbox.nowrap .lg{display:block}
.logbox.nowrap .lg-t,.logbox.nowrap .lg-l,.logbox.nowrap .lg-m{
  display:inline-block;margin-right:8px}
.lg.hidden{display:none}

/* ---------- 多模型 / 搜索引擎 卡片 ---------- */
.cardrow{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.pcard{flex:1;min-width:230px;border:1px solid var(--line);border-radius:8px;padding:11px 13px;
  background:var(--panel2);position:relative}
.pcard.cur{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc) inset}
.pcard .pn{font-weight:600;font-size:13.5px;margin-bottom:3px;display:flex;align-items:center;gap:6px}
.pcard .pm{font-family:var(--mono);font-size:11px;color:var(--dim);margin-bottom:5px}
.pcard .pd{font-size:11.5px;color:var(--mut);line-height:1.55;min-height:34px}
.pcard .pf{margin-top:9px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.pcard .pf .mini{padding:2px 9px}
.badge{font-size:10px;padding:1px 6px;border-radius:99px;border:1px solid var(--line);color:var(--mut)}
.badge.ok{border-color:var(--ok);color:var(--ok)}
.badge.warn{border-color:var(--warn);color:var(--warn)}
.badge.acc{border-color:var(--acc);color:var(--acc)}
.qbar{height:6px;border-radius:6px;background:var(--panel);overflow:hidden;margin-top:5px;
  border:1px solid var(--line)}
.qbar i{display:block;height:100%;background:var(--ok)}
.qbar i.hot{background:var(--warn)} .qbar i.full{background:var(--err)}

/* ---------- 功能健康度（回归测试结果）---------- */
.health{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.hbar{flex:1;min-width:180px;height:9px;border-radius:9px;overflow:hidden;
  display:flex;background:var(--panel2);border:1px solid var(--line)}
.hbar i{display:block;height:100%}
.hbar .ok{background:var(--ok)} .hbar .bad{background:var(--err)}
.hbar .sk{background:var(--dim);opacity:.5}
.hitem{display:flex;align-items:center;gap:7px;padding:5px 9px;border-radius:6px;
  border:1px solid var(--line);font-size:12px;margin:4px 5px 0 0;background:var(--panel2)}
.hitem.FAIL{border-color:var(--err);color:var(--err)}
.hitem.SKIP{opacity:.55}
.dotm{width:7px;height:7px;border-radius:50%;flex:0 0 7px}
.dotm.PASS{background:var(--ok)} .dotm.FAIL{background:var(--err)} .dotm.SKIP{background:var(--dim)}

/* ---------- 记忆库入口卡片（条目已挪到独立页 /memory）---------- */
.memgate{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.memgate-n{font-size:30px;font-weight:700;line-height:1.1;font-family:var(--mono)}
.memgate .btn{white-space:nowrap}
a.btn{text-decoration:none;display:inline-block}

/* ---------- 独立记忆页 ---------- */
.mwrap{max-width:1080px;margin:0 auto;padding:20px 22px 60px}
.mbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.mbar input[type=text]{flex:1;min-width:200px}
.mstat{display:flex;gap:14px;align-items:center;font-size:12px;color:var(--mut);
  margin-left:auto;white-space:nowrap}
.pager{display:flex;gap:8px;align-items:center;justify-content:center;
  margin-top:14px;font-size:13px;color:var(--mut)}
.tbl{width:100%;border-collapse:collapse;font-size:13px}
.tbl th{text-align:left;font-weight:500;color:var(--mut);font-size:12px;padding:6px 8px;
  border-bottom:1px solid var(--line)}
.tbl td{padding:6px 8px;border-bottom:1px solid var(--line)}
.tbl tr:last-child td{border-bottom:none}

/* ---------- 提示 ---------- */
#toast{position:fixed;right:20px;bottom:20px;z-index:99;display:flex;flex-direction:column;gap:8px}
.toast{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--acc);
  border-radius:8px;padding:10px 15px;font-size:13px;box-shadow:0 6px 24px rgba(0,0,0,.28);
  animation:sl .2s ease-out;max-width:340px}
.toast.ok{border-left-color:var(--ok)} .toast.err{border-left-color:var(--err)}
@keyframes sl{from{transform:translateX(16px);opacity:0}to{transform:none;opacity:1}}
.empty{color:var(--dim);font-size:13px;padding:14px 0;text-align:center}
"""

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小智控制台</title>
<style>
/*__SHARED_CSS__*/
</style></head><body>

<header><div class="hdr">
  <div class="brand">小智控制台 <small>xiaozhi deskemoji</small></div>
  <nav class="nav">
    <a href="#ov">概览</a><a href="#models">大模型</a><a href="#engines">搜索</a><a href="#workdir">过程文档</a><a href="#usage">用量</a><a href="#health">健康度</a><a href="#cfg">引擎配置</a>
    <a href="/memory" target="_blank">记忆库 ↗</a>
    <a href="/flash" target="_blank">固件烧录 ↗</a>
    <a href="#dev">设备</a><a href="#task">任务</a><a href="#log">日志</a>
  </nav>
  <div class="hdr-r">
    <span class="dot" id="dOnline"><i></i><span>设备</span></span>
    <span class="mono" id="now"></span>
    <button class="mini" onclick="load()">刷新</button>
  </div>
</div></header>

<div class="wrap">

<div class="metrics" id="metrics"></div>

<div class="cols">
<div>

  <section id="cfg"><div class="card">
    <h2>引擎配置 <span class="hint" id="cfgHint"></span></h2>
    <div class="bd">
      <div style="display:flex;gap:8px;margin-bottom:11px">
        <input id="q" placeholder="搜索配置项，例如 语速 / 记忆 / 超时" oninput="renderCfg()">
        <button class="pri" onclick="saveCfg()">保存改动</button>
        <button onclick="load()">放弃修改</button>
      </div>
      <div id="config"></div>
    </div>
  </div></div>

  <section id="mem"><div class="card">
    <h2>记忆库 <span class="hint" id="memcount"></span></h2>
    <div class="bd">
      <div class="memgate">
        <div>
          <div class="memgate-n" id="memTotal">—</div>
          <div class="hint">条记忆已沉淀</div>
        </div>
        <div style="flex:1" id="memMini"></div>
        <a class="btn pri" href="/memory" target="_blank">打开记忆库管理页 →</a>
      </div>
      <div class="hint" style="margin-top:9px">
        记忆条目较多，已挪到<b>独立页面</b>：可搜索、按分类筛选、翻页、逐条编辑删除。
      </div>
    </div>
  </div></div>

  <section id="log"><div class="card">
    <h2>运行日志 <span class="hint" id="loghint"></span></h2>
    <div class="bd">
      <div class="logbar">
        <input id="logq" placeholder="过滤日志（模块名 / 关键字）" oninput="renderLogs()">
        <select id="loglevel" onchange="renderLogs()">
          <option value="">全部级别</option>
          <option>DEBUG</option><option>INFO</option>
          <option>WARNING</option><option>ERROR</option>
        </select>
        <label class="chk"><input type="checkbox" id="logwrap" onchange="renderLogs()">换行显示</label>
        <span class="hint" id="logn"></span>
      </div>
      <div class="logbox" id="log">加载中…</div>
      <div id="logerr"></div>
    </div>
  </div></section>

</div>
<div>

  <section id="models"><div class="card">
    <h2>大模型 <span class="hint">切档案 = 改这一项，保存后重启后台生效</span></h2>
    <div class="bd">
      <div class="cardrow" id="llmCards"></div>
      <div class="hint">想加新模型？编辑项目配置 <code>server/xiaozhi-server/data/.config.yaml</code>
        的 <code>llm_profiles</code> 段，加一段就自动出现在这里（不用改代码）。
        填完 key 记得重启后台。</div>
    </div>
  </div></div>

  <section id="engines"><div class="card">
    <h2>搜索引擎 <span class="hint">按顺序尝试，谁先通用谁</span></h2>
    <div class="bd">
      <div class="cardrow" id="engCards"></div>
      <div id="engQuota"></div>
      <div class="hint" style="margin-top:9px">
        主力是 <b>Firecrawl keyless</b>：免注册免 key，1000 次/月，够用。
        开关在配置 <code>plugins.web_search.engines.&lt;引擎&gt;.enabled</code>，
        顺序看 <code>order</code>。额度账本记在 <code>bridge/search_quota.json</code>。
      </div>
    </div>
  </div></div>

  <section id="workdir"><div class="card">
    <h2>过程文档 <span class="hint">小智工作产生的临时资料</span></h2>
    <div class="bd">
      <div class="health" id="wdBar"></div>
      <div id="wdKinds" class="cardrow" style="margin-top:10px"></div>
      <div class="hint" style="margin-top:9px">
        目录：<code id="wdPath"></code><br>
        清理方式：直接删这个文件夹，或跟小智说「<b>清理过程文档</b>」（它会先报数量再问你确认）。
        <b>不会碰你其他任何文件。</b>
      </div>
    </div>
  </div></div>

  <section id="usage"><div class="card">
    <h2>大模型用量 <span class="hint">阿里云/DeepSeek 都不给查额度的 API，所以自己记</span></h2>
    <div class="bd">
      <div class="cardrow" id="useCards"></div>
      <div id="useAlert"></div>
      <div class="hint" style="margin-top:8px">
        记账文件：<code>bridge/llm_usage.json</code>（按天分桶，保留 60 天）。
        账本只反映<b>本机调用量</b>，不等于官方账单 —— 别的设备/网页端用同一账号不在其中。
      </div>
    </div>
  </div></div>

  <section id="health"><div class="card">
    <h2>功能健康度 <span class="hint" id="hTs"></span></h2>
    <div class="bd">
      <div class="health" id="hBar"></div>
      <div id="hItems" class="scroll-y" style="max-height:230px;overflow-y:auto"></div>
      <div class="hint" style="margin-top:9px">
        每项都是<b>实际调用一次</b>验出来的，不是看代码猜的。命令：
        <code>python tools/selftest.py --with-net</code>（联网项会消耗 Tavily 额度，默认跳过）
      </div>
    </div>
  </div></div>

  <section id="ov"><div class="card">
    <h2>服务与设备</h2>
    <div class="bd" id="dev">加载中…</div>
  </div></div>

  <section id="task"><div class="card">
    <h2>主动播报 <span class="hint">测一句话 / 提醒自己</span></h2>
    <div class="bd">
      <div style="display:flex;gap:8px;margin-bottom:10px">
        <input id="msg" placeholder="对它说一句话，会用当前音色播出来">
        <button class="pri" onclick="sendMsg()">播报</button>
      </div>
      <div id="cmsg" class="hint"></div>
    </div>
  </div></div>

  <section><div class="card">
    <h2>任务投递箱 <span class="hint" id="taskCount"></span></h2>
    <div class="bd"><div id="tasks"><div class="empty">加载中…</div></div></div>
  </div></section>

</div>
</div>
</div>
<div id="toast"></div>

<script>
let STATE=null, MEM={items:[]}, memKind='', dirty={};

function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function toast(msg,kind){
  const d=document.createElement('div'); d.className='toast '+(kind||''); d.textContent=msg;
  document.getElementById('toast').appendChild(d);
  setTimeout(()=>d.remove(),3200);
}
function setDot(id,on,txt){const e=document.getElementById(id);e.className='dot '+(on?'on':'off');
  if(txt!==undefined)e.querySelector('span').textContent=txt;}

// ---------- 加载 ----------
async function load(){
  try{
    const r=await fetch('/api/state'); STATE=await r.json();
  }catch(e){ toast('无法连接管理台服务','err'); return; }
  const c=STATE.config||{}, ed=STATE.editable||{}, gs=STATE.groups||[];
  const online=(STATE.online||[]).length>0;
  setDot('dOnline',online,online?('设备在线 '+(STATE.online||[])[0].slice(-5)):'设备离线');
  document.getElementById('now').textContent=new Date().toLocaleTimeString('zh-CN',{hour12:false});
  renderMetrics(c,online); renderDev(); renderTasks(); renderLogs(); renderHealth(); renderLLM(); renderEngines(); renderWorkdir(); renderUsage();
  if(!dirtyCount()) renderCfg();
  loadMem();
}
function dirtyCount(){return Object.keys(dirty).length;}

// ---------- 概览指标 ----------
function renderMetrics(c,online){
  const mem=STATE.memory_stats||{};
  const m=[
    {k:'耳朵 ASR',v:c.ASR||'-',s:'语音识别',ok:true},
    {k:'大脑 LLM',v:c.LLM||'-',s:'思考与决策',ok:true},
    {k:'嘴 TTS',v:c.TTS||'-',s:'语音合成',ok:true},
    {k:'记忆',v:c.Memory||'nomem',s:'自研 bridge 层',ok:true},
    {k:'记忆条数',v:(mem.total!=null?mem.total:'-'),s:memObj(),ok:true},
    {k:'未完成待办',v:(STATE.todo&&STATE.todo.pending!=null?STATE.todo.pending:'-'),s:'小智在跟进',ok:true},
  ];
  document.getElementById('metrics').innerHTML=m.map(x=>
    `<div class="metric"><div class="k">${esc(x.k)}</div><div class="v" style="font-size:16px">${esc(String(x.v))}</div><div class="s">${esc(x.s)}</div></div>`
  ).join('');
}
function memObj(){
  const m=STATE.memory_stats||{}; if(!m.total) return '点击下方查看';
  const p=[]; for(const k of ['chat','profile','project','knowledge','task'])
    if(m.by_kind&&m.by_kind[k]) p.push(k+' '+m.by_kind[k]);
  return p.join(' · ');
}

// ---------- 配置 ----------
function renderCfg(){
  if(!STATE) return;
  const ed=STATE.editable||{}, groups=STATE.groups||[], q=(document.getElementById('q').value||'').trim().toLowerCase();
  let h='';
  groups.forEach(g=>{
    const items=Object.keys(ed).filter(k=>ed[k].group===g.id).filter(k=>{
      if(!q) return true;
      const m=ed[k];
      return (m.label||k).toLowerCase().includes(q) || k.toLowerCase().includes(q)
        || (m.desc||'').toLowerCase().includes(q);
    });
    if(!items.length) return;
    h+=`<details class="grp"${q?' open':''}><summary>${esc(g.name)}
      <span class="hint" style="font-weight:400">${esc(g.desc||'')}</span></summary><div class="grp-bd">`;
    items.forEach(k=>{ h+=cfgRow(k,ed[k]); });
    h+='</div></details>';
  });
  document.getElementById('config').innerHTML=h||'<div class="empty">没有匹配的配置项</div>';
  document.getElementById('cfgHint').textContent=dirtyCount()?('有 '+dirtyCount()+' 项未保存'):'';
}
function cfgRow(k,m){
  const c=STATE.config||{}, v=c[k];
  const d=m.desc||'';
  let ctl;
  if(m.type==='enum'&&m.choices&&m.choices.length){
    const notes=(m.notes&&m.notes[k])||{};
    ctl=`<select data-k="${k}">${m.choices.map(x=>
      `<option value="${esc(x)}"${String(x)===String(v)?' selected':''}>${esc(x)}${notes[x]?' — '+esc(notes[x]):''}</option>`).join('')}</select>`;
  }else if(m.type==='bool'){
    ctl=`<label class="chk"><input type="checkbox" data-k="${k}"${v?' checked':''}> ${v?'开':'关'}</label>`;
  }else if(m.type==='time'){
    // 2026-10-04 免打扰时段：用原生时间选择器，省得手输格式出错
    ctl=`<input type="time" data-k="${k}" value="${esc(v==null?'':v)}">`;
  }else if(m.type==='float'||m.type==='int'){
    ctl=`<input type="${m.type}" data-k="${k}" value="${esc(v==null?'':v)}">`;
  }else{
    ctl=`<input data-k="${k}" value="${esc(v==null?'':v)}">`;
  }
  const ch=dirty[k]?' style="color:var(--warn)"':'';
  return `<div class="row"><div class="lb"${ch}>${esc(m.label||k)}<small>${esc(d)}</small></div>
    <div class="ctl">${ctl}</div></div>`;
}
async function saveCfg(){
  const btns=[...document.querySelectorAll('#config [data-k]')];
  const body={};
  btns.forEach(el=>{
    const k=el.dataset.k, t=el.type;
    let v;
    if(t==='checkbox') v=el.checked;
    else v=el.value;
    if(String(v)!==String(STATE.config[k]??'')) body[k]=v;
  });
  if(!Object.keys(body).length){ toast('没有改动'); return; }
  const r=await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  const j=await r.json();
  if(j.ok){ dirty={}; toast('已保存并备份原配置','ok'); load(); }
  else toast('保存失败：'+(j.message||j.error||''),'err');
}
document.addEventListener('input',e=>{
  const k=e.target.dataset&&e.target.dataset.k; if(k){dirty[k]=1;document.getElementById('cfgHint').textContent='有 '+dirtyCount()+' 项未保存';}
});

// ---------- 设备 ----------
function renderDev(){
  const s=STATE, el=document.getElementById('dev');
  const ports=(s.ports||[]).map(p=>
    `<tr><td class="mono">${p.port||p[0]}</td><td>${esc(p.name||p[1]||'')}</td>
     <td>${p.ok?'<span class="tag" style="color:var(--ok);border-color:var(--ok)">正常</span>'
             :'<span class="tag" style="color:var(--err);border-color:var(--err)">未开</span>'}</td></tr>`).join('');
  el.innerHTML=`
   <table class="tbl"><tr><td>设备</td><td>${(s.online||[]).join(', ')||'<span class="hint">离线</span>'}</td></tr>
   <tr><td>IP</td><td class="mono">${esc(s.device_ip||'-')}</td></tr>
   <tr><td>版本</td><td class="mono">${esc(s.version||'-')}</td></tr></table>
   <div style="margin:11px 0 7px;font-size:12px;color:var(--mut)">端口</div>
   <table class="tbl"><tr><th>端口</th><th>用途</th><th>状态</th></tr>${ports||'<tr><td colspan=3 class="hint">无</td></tr>'}</table>
   <div style="margin-top:12px;display:flex;gap:8px">
     <button onclick="svc('stop')">停止后台</button>
     <button class="pri" onclick="svc('start')">启动后台</button>
   </div>`;
}
async function svc(a){
  const j=await (await fetch('/api/service',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:a})})).json();
  toast(j.message||(j.ok?'ok':'失败'),j.ok?'ok':'err');
}
function renderTasks(){
  const t=STATE.tasks||[];
  document.getElementById('taskCount').textContent=t.length?`${t.length} 条`:'';
  document.getElementById('tasks').innerHTML = t.length
    ? t.map(x=>`<div class="mem"><div class="memh"><span class="tag k-${x.status==='done'?'knowledge':(x.status==='failed'?'task':'project')}">${esc(x.status||'')}</span>
        <span class="mono hint" style="display:inline">${esc((x.tid||'').slice(-6))}</span></div>
       <div class="memc">${esc(x.title||x.text||'')}</div></div>`).join('')
    : '<div class="empty">没有任务</div>';
}
// ---------- 日志：一行一条 ----------
function logRow(x){
  const q=(logQ||'').toLowerCase();
  if(logLvl && x.lvl!==logLvl) return '';
  if(q && !((x.mod||'')+' '+(x.msg||'')+' '+(x.time||'')).toLowerCase().includes(q)) return '';
  return `<div class="lg"><span class="lg-t">${esc(x.time||'')}</span>`
    +`<span class="lg-l ${esc(x.lvl||'INFO')}">${esc(x.lvl||'')}</span>`
    +`<span class="lg-m" title="${esc(x.mod||'')}">${esc(x.mod||'')}</span>`
    +`<span class="lg-x">${esc(x.msg||'')}</span></div>`;
}
function renderLogs(){
  const el=document.getElementById('log');
  const all=STATE.log||[];
  const html=all.map(logRow).filter(Boolean).join('');
  el.innerHTML = html || '<div class="empty">没有匹配的日志</div>';
  el.classList.toggle('nowrap', !logWrap);
  const n=document.getElementById('logn');
  if(n) n.textContent = `显示 ${html?html.split('<div').length-1:0} / ${all.length} 条`;
  const e=STATE.log_err||[];
  const ebox=document.getElementById('logerr');
  ebox.innerHTML = e.length
    ? '<div class="hint" style="margin:10px 0 5px;color:var(--err)">错误输出（stderr）</div>'
      +'<div class="logbox nowrap" style="max-height:180px;color:var(--err)">'
      + e.map(x=>`<div class="lg"><span class="lg-t">${esc(x.time||'')}</span>`
          +`<span class="lg-l ERROR">ERR</span>`
          +`<span class="lg-x">${esc(x.msg||'')}</span></div>`).join('')
      + '</div>'
    : '';
  // 新行在底部时自动跟到底
  if(!logWrap) el.scrollTop=el.scrollHeight;
}
let logQ='', logLvl='', logWrap=false;
document.addEventListener('DOMContentLoaded',()=>{
  const q=document.getElementById('logq'), lv=document.getElementById('loglevel'), w=document.getElementById('logwrap');
  if(q) q.addEventListener('input',()=>{logQ=q.value.trim();renderLogs();});
  if(lv) lv.addEventListener('change',()=>{logLvl=lv.value;renderLogs();});
  if(w) w.addEventListener('change',()=>{logWrap=w.checked;renderLogs();});
});

// ---------- 大模型档案 ----------
function renderLLM(){
  const d=STATE.llm_profiles||{}, el=document.getElementById('llmCards');
  const cur=(STATE.config||{}).LLM;
  const ks=Object.keys(d);
  if(!ks.length){ el.innerHTML='<div class="empty">配置里还没有 llm_profiles</div>'; return; }
  el.innerHTML=ks.map(k=>{const p=d[k];return `
    <div class="pcard ${p.current?'cur':''}">
      <div class="pn">${esc(p.label)}
        ${p.current?'<span class="badge acc">使用中</span>':''}
        ${p.key_missing?'<span class="badge warn">缺 key</span>':'<span class="badge ok">key 就绪</span>'}
      </div>
      <div class="pm">${esc(k)}${p.model?' · '+esc(p.model):''}</div>
      <div class="pd">${esc(p.note||'—')}</div>
      <div class="pf">
        ${p.current?'':`<button class="mini" onclick="switchLLM('${k}')">切到它</button>`}
        ${p.current?'<span class="hint">保存配置后重启后台生效</span>':''}
      </div>
    </div>`}).join('');
}
async function switchLLM(k){
  if(!confirm('把大脑切到 '+k+' ？保存后需要重启后台才生效。')) return;
  const j=await (await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({LLM:k})})).json();
  toast(j.message||(j.ok?'已保存，重启后台生效':'失败'), j.ok?'ok':'err');
  if(j.ok) setTimeout(load,600);
}

// ---------- 搜索引擎 ----------
function renderEngines(){
  const d=STATE.search_engines||{}, el=document.getElementById('engCards');
  const ord=d.order||[], en=d.engines||{};
  const ks=ord.length?ord:Object.keys(en);
  if(!ks.length){ el.innerHTML='<div class="empty">配置里还没有 engines</div>'; return; }
  const LAB={firecrawl:'Firecrawl',tavily:'Tavily',metaso:'秘塔',serply:'Serply',deepseek:'DeepSeek 搜索'};
  el.innerHTML=ks.map((k,i)=>{const e=en[k]||{};return `
    <div class="pcard ${i===0&&e.enabled?'cur':''}">
      <div class="pn">${i+1}. ${esc(LAB[k]||k)}
        ${i===0&&e.enabled?'<span class="badge acc">主力</span>':''}
        ${e.enabled?'':'<span class="badge">已停用</span>'}
        ${!e.has_key&&k!=='firecrawl'?'<span class="badge warn">缺 key</span>':''}
      </div>
      <div class="pd">${esc(e.note||'—')}</div>
    </div>`}).join('');
  const q=d.quota||[];
  if(q.length){
    document.getElementById('engQuota').innerHTML =
      '<div class="hint" style="margin:4px 0 6px">本月额度（每次成功搜索计一次，Tavily 仅作备用）</div>'
      + q.map(x=>{
          const p=Math.min(100,x.percent);
          const cls=p>=90?'full':(p>=75?'hot':'');
          return `<div style="margin-bottom:7px">
            <div style="display:flex;font-size:12px">
              <span>${esc(x.engine)}</span>
              <span style="margin-left:auto" class="mono">${x.used} / ${x.quota}（剩 ${x.left}）</span>
            </div>
            <div class="qbar"><i class="${cls}" style="width:${p}%"></i></div></div>`;
        }).join('');
  }
}

// ---------- 大模型用量 ----------
const USE_LAB={qwen:'通义千问',deepseek:'DeepSeek',openai:'OpenAI 兼容'};
function renderUsage(){
  const d=STATE.llm_usage||{}, t=d.today||{}, el=document.getElementById('useCards');
  const ks=Object.keys(t);
  if(!ks.length){
    el.innerHTML='<div class="empty" style="width:100%">今天还没有调用记录</div>';
  } else {
    el.innerHTML=ks.map(k=>{
      const v=t[k], kp=(v.tokens/1000).toFixed(1);
      return `<div class="pcard"><div class="pn">${esc(USE_LAB[k]||k)}
        <span class="badge">${v.calls} 次</span></div>
        <div class="pm" style="font-size:15px">${kp} K token</div>
        <div class="pd">单次最大 ${v.max_single||0} token</div></div>`;
    }).join('');
  }
  const a=d.alerts||[];
  document.getElementById('useAlert').innerHTML = a.length
    ? a.map(x=>`<div class="toast err" style="margin-top:8px;position:static">⚠️ ${esc(x)}</div>`).join('')
    : '';
}

// ---------- 过程文档目录 ----------
function renderWorkdir(){
  const d=STATE.workdir||{}, bar=document.getElementById('wdBar');
  if(!d.ok){ bar.innerHTML='<span class="hint">'+(d.message||'读取失败')+'</span>'; return; }
  const mb=(d.total_bytes||0)/1024/1024;
  bar.innerHTML=`<b style="font-size:22px;font-family:var(--mono)">${d.total_files||0}</b>`
    +`<span class="hint">个文件 · 合计 ${mb<0.1?'<0.1':mb.toFixed(1)} MB</span>`
    +`<span class="hint">（清理过 ${d.cleanup_count||0} 次${d.last_cleanup?'，最近 '+d.last_cleanup:''}）</span>`;
  const ks=d.kinds||{};
  document.getElementById('wdKinds').innerHTML=Object.keys(ks).map(k=>{
    const v=ks[k];
    return `<div class="pcard"><div class="pn">${esc(k)}
      <span class="badge ${v.files?'ok':''}">${v.files} 个</span></div>
      <div class="pm">${v.bytes?(v.bytes/1024).toFixed(1)+' KB':'空'}</div></div>`;
  }).join('');
  document.getElementById('wdPath').textContent=d.root||'';
}

// ---------- 功能健康度（回归测试）----------
function renderHealth(){
  const t=STATE.selftest||{};
  const bar=document.getElementById('hBar');
  if(!t.ok){ bar.innerHTML='<span class="hint">还没跑过回归测试，'
    +'在项目目录执行 <code>python tools/selftest.py --with-net</code></span>'; return; }
  const tot=(t.pass||0)+(t.fail||0)+(t.skip||0);
  const pct=x=> tot? (x/tot*100).toFixed(1):'0';
  bar.innerHTML=
      `<b style="font-size:22px;font-family:var(--mono)">${t.pass}/${tot}</b>`
    + `<div class="hbar"><i class="ok" style="width:${pct(t.pass)}%"></i>`
    + `<i class="bad" style="width:${pct(t.fail)}%"></i>`
    + `<i class="sk" style="width:${pct(t.skip)}%"></i></div>`
    + `<span class="hint">${t.fail?('<b style="color:var(--err)">'+t.fail+' 项故障</b>'):'全部正常'}</span>`;
  document.getElementById('hTs').textContent='测试于 '+t.ts;
  document.getElementById('hItems').innerHTML=(t.items||[]).map(i=>
    `<div class="hitem ${i.status}" title="${esc(i.detail||'')}">`
    +`<span class="dotm ${i.status}"></span>${esc(i.name)}`
    +`<span class="hint">${esc((i.detail||'').slice(0,54))}</span></div>`).join('');
}

// ---------- 记忆（主页面只放入口卡片）----------
async function loadMem(){
  try{
    const r=await fetch('/api/memory?limit=1');
    const j=await r.json();
    if(!j.ok) return;
    const c=j.counts||{};
    const t=document.getElementById('memTotal');
    if(t) t.textContent=j.total;
    const m=document.getElementById('memMini');
    if(m) m.innerHTML=['profile','knowledge','project','chat','task']
      .filter(k=>c[k]).map(k=>`<span class="tag k-${k}" style="margin:2px 4px 2px 0">${k} ${c[k]}</span>`).join('');
  }catch(e){}
}
// 2026-10-03：记忆的增删改已挪到独立页 /memory，这里不再重复实现
// （addMemory/setMemKind 会引用已不存在的 DOM 元素，留着就是死代码）。
// editMemory/saveMemory/delMemory 保留 —— 独立页有自己的同名函数，不冲突。
function editMemory(id){
  const box=document.getElementById('mc'+id); if(!box||box.dataset.ed) return; box.dataset.ed='1';
  box.innerHTML=`<textarea style="min-height:64px">${esc(box.textContent)}</textarea>
    <div style="margin-top:6px"><button class="pri mini" onclick="saveMemory(${id})">保存</button>
    <button class="mini" onclick="loadMem()">取消</button></div>`;
}
async function saveMemory(id){
  const v=document.querySelector('#mc'+id+' textarea').value;
  const j=await (await fetch('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'edit',id:id,content:v})})).json();
  toast(j.message||'已保存',j.ok?'ok':'err'); loadMem();
}
async function delMemory(id){
  if(!confirm('确定删除这条记忆？不可恢复。')) return;
  const j=await (await fetch('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'delete',id:id})})).json();
  toast(j.message||'已删除',j.ok?'ok':'err'); loadMem();
}

// ---------- 播报 ----------
async function sendMsg(){
  const el=document.getElementById('msg'); const t=el.value.trim(); if(!t) return;
  document.getElementById('cmsg').textContent='下发中…';
  try{
    const r=await fetch('http://'+location.hostname+':8004/notify',
      {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t})});
    const j=await r.json();
    document.getElementById('cmsg').textContent = j.queued? '设备不在线，已排队等它上线补播' : '已下发到设备';
    toast(j.queued?'已排队':'已播报',j.queued?'':'ok');
  }catch(e){ document.getElementById('cmsg').textContent='下发失败：'+e.message; }
}

// ---------- 启动 ----------
load(); setInterval(load,15000);
setTimeout(loadMem,600);
</script></body></html>"""


# ============================================================ 记忆管理
# 2026-10-03 13:30 补回：重构页面时这段被整块替换误删，导致 /api/memory 报 NameError。
_MEM_MOD = None


def _bridge_dir():
    for up in (2, 3, 4):
        p = (Path(__file__).resolve().parents[up]) / "bridge"
        if p.exists():
            return p
    return Path(__file__).resolve().parents[2] / "bridge"


def _mem():
    """懒加载 bridge/memory.py（管理台不能因为记忆库坏了就起不来）。"""
    global _MEM_MOD
    if _MEM_MOD is not None:
        return _MEM_MOD or None
    try:
        m = _load_bridge_mod("memory.py")
        _MEM_MOD = m if m is not None else False
    except Exception:
        _MEM_MOD = False
    return _MEM_MOD or None


def _q(path, key, default=""):
    if "?" not in path:
        return default
    from urllib.parse import parse_qs, urlparse
    v = parse_qs(urlparse(path).query).get(key)
    return v[0] if v else default


def memory_list(kind="", limit=60, offset=0, query=""):
    m = _mem()
    if m is None:
        return {"ok": False, "message": "记忆模块不可用", "items": [], "counts": {}}
    try:
        c = m.connect()
        by = {r[0]: r[1] for r in
              c.execute("SELECT kind, COUNT(*) n FROM memories GROUP BY kind").fetchall()}
        # 2026-10-03 记忆库独立页面：关键词搜索 + 分页。
        # ⚠️ 不用 memory.search() —— 它会对**全库每一条**算 embed（打 Ollama），
        #    管理台每敲一个字就打一次模型，页面直接卡死。这里用 SQL LIKE，毫秒返回。
        where, args = [], []
        if kind:
            where.append("kind=?")
            args.append(kind)
        if query:
            where.append("content LIKE ?")
            args.append("%" + str(query) + "%")
        sql = ("SELECT id, ts, kind, content, tags FROM memories"
               + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY id DESC LIMIT ? OFFSET ?")
        rows = [dict(id=r[0], ts=r[1], kind=r[2], content=r[3], tags=r[4])
                for r in c.execute(sql, (*args, int(limit), max(0, int(offset)))).fetchall()]
        total = sum(counts_of(by, kind, query, m).values()) if (query or kind) else sum(by.values())
        c.close()
        counts = {k: by.get(k, 0) for k in
                  ("chat", "profile", "project", "knowledge", "task")}
        return {"ok": True, "items": rows, "counts": counts,
                "total": sum(counts.values()), "matched": total,
                "offset": max(0, int(offset)), "limit": int(limit),
                "has_more": max(0, int(offset)) + len(rows) < total}
    except Exception as e:
        return {"ok": False, "message": str(e), "items": [], "counts": {}}


def counts_of(by, kind, query, m):
    """按当前筛选条件算各分类命中数（用于 chips 上的数字）。"""
    if not (query or kind):
        return by
    out = {}
    for k in ("chat", "profile", "project", "knowledge", "task"):
        sql = "SELECT COUNT(*) n FROM memories WHERE kind=?"
        args = [k]
        if query:
            sql += " AND content LIKE ?"
            args.append("%" + str(query) + "%")
        try:
            c = m.connect()
            out[k] = c.execute(sql, args).fetchone()[0]
            c.close()
        except Exception:
            out[k] = 0
    return out


def memory_add(content, kind="knowledge", tags=""):
    m = _mem()
    if m is None:
        return {"ok": False, "message": "记忆模块不可用"}
    content = (content or "").strip()
    if not content:
        return {"ok": False, "message": "内容不能为空"}
    mid = m.add(kind, content, tags)
    return {"ok": bool(mid), "message": "已添加" if mid else "添加失败", "id": mid}


def memory_edit(mid, content):
    m = _mem()
    if m is None:
        return {"ok": False, "message": "记忆模块不可用"}
    content = (content or "").strip()
    if not content:
        return {"ok": False, "message": "内容不能为空"}
    try:
        c = m.connect()
        # 内容改了必须重算向量，否则按语义搜就搜不到了
        c.execute("UPDATE memories SET content=?, embed=? WHERE id=?",
                  (content, m._pack(m.embed(content)), int(mid)))
        c.execute("DELETE FROM memories_fts WHERE rowid=?", (int(mid),))
        c.execute("INSERT INTO memories_fts(rowid, content, tags)"
                  " SELECT id, content, tags FROM memories WHERE id=?", (int(mid),))
        c.commit()
        c.close()
        return {"ok": True, "message": "已保存"}
    except Exception as e:
        return {"ok": False, "message": "保存失败: %s" % e}


def memory_delete(mid):
    m = _mem()
    if m is None:
        return {"ok": False, "message": "记忆模块不可用"}
    try:
        c = m.connect()
        c.execute("DELETE FROM memories WHERE id=?", (int(mid),))
        c.execute("DELETE FROM memories_fts WHERE rowid=?", (int(mid),))
        c.commit()
        c.close()
        return {"ok": True, "message": "已删除"}
    except Exception as e:
        return {"ok": False, "message": "删除失败: %s" % e}


# 把主页面里的 CSS 占位符换成真样式（必须在 INDEX_HTML 定义之后）
INDEX_HTML = INDEX_HTML.replace("/*__SHARED_CSS__*/", SHARED_CSS)


# ==================== 记忆库独立页（2026-10-03）====================
# 主人原话「记忆库里边也是加个滚动条。记忆库可以单独做一个页面，
# 如果因为 test 社交内容比较多的话，你再整合一下」。
# 做法：/memory 独立页，条目多时用「分页 + 固定高度滚动区」，
# 搜索走 SQL LIKE（**不用 memory.search()**，那个会对全库每条算 embed、打 Ollama，
# 边打字边打模型会把页面卡死）。
MEMORY_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>记忆库 · 小智控制台</title>
<style>/*__SHARED_CSS__*/</style></head><body>

<header><div class="hdr">
  <div class="brand">记忆库 <small>xiaozhi deskemoji</small></div>
  <nav class="nav"><a href="/">← 返回控制台</a></nav>
  <div class="hdr-r">
    <span class="mono hint" id="upd"></span>
    <button class="mini" onclick="load(0)">刷新</button>
  </div>
</div></header>

<div class="mwrap">

  <div class="card" style="margin-bottom:14px"><div class="bd">
    <div class="mbar">
      <input type="text" id="q" placeholder="搜索记忆内容，例如 舵机 / 火山 / 待办"
             oninput="debounceLoad()">
      <select id="kind" onchange="load(0)">
        <option value="">全部分类</option>
        <option value="profile">profile（人物设定）</option>
        <option value="knowledge">knowledge（知识）</option>
        <option value="project">project（项目）</option>
        <option value="chat">chat（对话）</option>
        <option value="task">task（任务）</option>
      </select>
      <select id="size" onchange="load(0)">
        <option value="20">每页 20 条</option>
        <option value="50" selected>每页 50 条</option>
        <option value="100">每页 100 条</option>
      </select>
      <span class="mstat" id="stat">加载中…</span>
    </div>
    <div class="chips" id="filter"></div>
  </div></div>

  <div class="card"><div class="bd">
    <div class="mbar">
      <input type="text" id="new" placeholder="手工添加一条记忆，例：他偏好简洁直接的说话方式"
             onkeydown="if(event.key==='Enter')add()">
      <select id="newkind" style="width:130px">
        <option value="knowledge">knowledge</option><option value="profile">profile</option>
        <option value="project">project</option><option value="chat">chat</option>
      </select>
      <button class="pri" onclick="add()">添加</button>
    </div>
    <!-- 固定高度 + 滚动：内容再多页面也不会被撑爆 -->
    <div class="scroll-y" id="list" style="max-height:calc(100vh - 330px);min-height:220px;
         overflow-y:auto;border:1px solid var(--line);border-radius:8px;padding:10px 12px">
      <div class="empty">加载中…</div>
    </div>
    <div class="pager">
      <button class="mini" onclick="load(0)">« 首页</button>
      <button class="mini" onclick="load(Math.max(0,OFF-1))">‹ 上一页</button>
      <span id="pg">—</span>
      <button class="mini" onclick="load(OFF+1)">下一页 ›</button>
      <button class="mini" onclick="load(LAST)">末页 »</button>
    </div>
  </div></div>

  <div class="hint" style="margin-top:14px">
    记忆由每日凌晨 03:10 的<b>蒸馏任务</b>自动汇总（小智对话 + 任务结果 + WorkBuddy 日志），
    判定为「思维层」的内容会同时写入 WorkBuddy 的 MEMORY.md，两边共享同一池子。
  </div>
</div>

<div id="toast"></div>
<script>
let OFF=0, LAST=0, TOTAL=0, timer=null;
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function toast(m,k){
  const d=document.createElement('div');
  d.className='toast '+(k||'ok'); d.textContent=m;
  document.getElementById('toast').appendChild(d);
  setTimeout(()=>d.remove(),2600);
}
function debounceLoad(){ clearTimeout(timer); timer=setTimeout(()=>load(0),260); }

async function load(off){
  const q=document.getElementById('q').value.trim();
  const kind=document.getElementById('kind').value;
  const size=document.getElementById('size').value;
  const el=document.getElementById('list');
  el.innerHTML='<div class="empty">加载中…</div>';
  let u='/api/memory?limit='+size+'&offset='+off
      +'&kind='+encodeURIComponent(kind)+'&q='+encodeURIComponent(q);
  try{
    const j=await (await fetch(u)).json();
    if(!j.ok){ el.innerHTML='<div class="empty">读取失败：'+esc(j.message||'')+'</div>'; return; }
    const items=j.items||[]; TOTAL=j.total||0; LAST=Math.max(0,Math.ceil(TOTAL/size)-1);
    OFF=Math.min(off,LAST);
    const c=j.counts||{};
    document.getElementById('filter').innerHTML=
      `<button class="chip${kind?'':' act'}" onclick="setKind('')">全部 ${TOTAL}</button>`
      +['profile','knowledge','project','chat','task'].map(k=>
        `<button class="chip${kind===k?' act':''}" onclick="setKind('${k}')">${k} ${c[k]||0}</button>`
      ).join('');
    document.getElementById('stat').textContent =
      (q||kind) ? `命中 ${j.matched!=null?j.matched:items.length} 条 / 共 ${TOTAL} 条`
                : `共 ${TOTAL} 条记忆`;
    document.getElementById('pg').textContent=`第 ${OFF+1} / ${LAST+1} 页`;
    document.getElementById('upd').textContent='更新于 '+new Date().toLocaleTimeString();
    el.innerHTML = items.length ? items.map(m=>`
      <div class="mem" data-id="${m.id}">
        <div class="memh">
          <span class="tag k-${esc(m.kind)}">${esc(m.kind)}</span>
          <span class="mono hint">${esc((m.ts||'').replace('T',' ').slice(0,16))}</span>
          ${m.tags?`<span class="hint">#${esc(m.tags)}</span>`:''}
          <span style="margin-left:auto">
            <button class="mini" onclick="edit(${m.id})">编辑</button>
            <button class="mini dgr" onclick="del(${m.id})">删除</button></span>
        </div>
        <div class="memc" id="mc${m.id}">${esc(m.content)}</div>
      </div>`).join('')
      : '<div class="empty">没有匹配的记忆</div>';
  }catch(e){ el.innerHTML='<div class="empty">读取失败：'+esc(e.message)+'</div>'; }
}
function setKind(k){ document.getElementById('kind').value=k; load(0); }

async function add(){
  const el=document.getElementById('new');
  if(!el.value.trim()) return;
  const j=await (await fetch('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'add',content:el.value,
      kind:document.getElementById('newkind').value})})).json();
  toast(j.message||(j.ok?'已添加':'失败'), j.ok?'ok':'err');
  if(j.ok){ el.value=''; load(0); }
}
function edit(id){
  const box=document.getElementById('mc'+id);
  if(!box||box.dataset.ed) return; box.dataset.ed='1';
  box.innerHTML=`<textarea style="min-height:70px;width:100%">${esc(box.textContent)}</textarea>
    <div style="margin-top:6px">
      <button class="pri mini" onclick="save(${id})">保存</button>
      <button class="mini" onclick="load(OFF)">取消</button></div>`;
}
async function save(id){
  const v=document.querySelector('#mc'+id+' textarea').value;
  const j=await (await fetch('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'edit',id:id,content:v})})).json();
  toast(j.message||'已保存', j.ok?'ok':'err');
  if(j.ok) load(OFF);
}
async function del(id){
  if(!confirm('确定删除这条记忆？不可恢复。')) return;
  const j=await (await fetch('/api/memory',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'delete',id:id})})).json();
  toast(j.message||'已删除', j.ok?'ok':'err');
  load(Math.min(OFF,Math.max(0,OFF-1)));
}
document.getElementById('size').value='50';
load(0);
</script>
</body></html>"""
MEMORY_HTML = MEMORY_HTML.replace("/*__SHARED_CSS__*/", SHARED_CSS)

class Handler(BaseHTTPRequestHandler):
    server_version = "XiaozhiAdmin/0.1"
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        if self.path in ("/memory", "/memory.html"):
            return self._send(200, MEMORY_HTML.encode("utf-8"), "text/html; charset=utf-8")
        # ★ 2026-10-03 23:20 固件烧录页（在线 OTA，用户端自助升级）
        # ⚠️ 导入方式说明：本文件是以**脚本**方式跑的（python admin_server.py），
        #    `admin` 包不在 sys.path 里，`from admin.flasher import ...` 会
        #    ModuleNotFoundError: No module named 'admin'（踩过一次）。
        #    所以用「同目录直接导入」——flasher.py 就在本文件旁边。
        if self.path in ("/flash", "/flash.html"):
            from flasher import FLASHER_HTML
            return self._send(200, FLASHER_HTML.encode("utf-8"), "text/html; charset=utf-8")
        if self.path.startswith("/api/flash"):
            from flasher import collect_flash_state
            return self._send(200, json.dumps(
                collect_flash_state(), ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8")
        if self.path.startswith("/api/state"):
            st = collect_state()
            st["editable"] = {
                k: {
                    "type": t,
                    "desc": d,
                    "choices": ENUM_CHOICES.get(k) if t == "enum" else None,
                    # 2026-10-03 新增：分组 / 中文标签 / 选项说明，供页面分组渲染
                    "group": GROUP_OF.get(k, "ui"),
                    "label": LABELS.get(k, k),
                    "notes": CHOICE_NOTES,
                }
                for k, _s, t, d in EDITABLE
            }
            st["groups"] = [
                {"id": gid, "name": gname, "desc": gdesc} for gid, gname, gdesc in GROUPS
            ]
            return self._json(st)
        if self.path.startswith("/api/memory"):
            return self._json(memory_list(
                kind=_q(self.path, "kind"),
                limit=int(_q(self.path, "limit") or 60),
                offset=int(_q(self.path, "offset") or 0),
                query=_q(self.path, "q") or ""))
        return self._json({"error": "not found"}, 404)

    # ---- 记忆管理（2026-10-03 主人要「能看到、能编辑」）----
    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception as e:
            return self._json({"ok": False, "message": "请求体解析失败: %s" % e}, 400)

        # ★ 2026-10-03 23:20 固件烧录页：编译 / 发布到 OTA
        #    同上：同目录直接导入，不用 admin.flasher（见 do_GET 里的说明）
        if self.path.startswith("/api/flash"):
            from flasher import build_firmware, publish_to_ota_dir
            if self.path.startswith("/api/flash/build"):
                return self._json(build_firmware())
            if self.path.startswith("/api/flash/publish"):
                return self._json(publish_to_ota_dir(payload.get("version", "")))
            return self._json({"ok": False, "message": "未知接口"}, 404)

        if self.path.startswith("/api/memory"):
            act = payload.get("action")
            try:
                if act == "add":
                    return self._json(memory_add(payload.get("content", ""),
                                                   payload.get("kind", "knowledge"),
                                                   payload.get("tags", "")))
                if act == "edit":
                    return self._json(memory_edit(int(payload.get("id", 0)),
                                                   payload.get("content", "")))
                if act == "delete":
                    return self._json(memory_delete(int(payload.get("id", 0))))
                return self._json({"ok": False, "message": "未知动作"}, 400)
            except Exception as e:
                return self._json({"ok": False, "message": "操作失败: %s" % e}, 500)

        if self.path.startswith("/api/service"):
            a = payload.get("action")
            if a == "start":
                ok, msg = start_server()
            elif a == "stop":
                ok, msg = stop_server()
            else:
                ok, msg = False, "未知动作: %s" % a
            return self._json({"ok": ok, "message": msg})

        if self.path.startswith("/api/config"):
            text = read_config_text()
            applied, skipped = [], []
            for k, _s, t, _d in EDITABLE:
                if k not in payload:
                    continue
                v = str(payload[k]).strip()
                if t == "int":
                    if not re.fullmatch(r"-?\d+", v):
                        skipped.append("%s=%s(非整数)" % (k, v))
                        continue
                elif t == "float":
                    if not re.fullmatch(r"-?\d+(\.\d+)?", v):
                        skipped.append("%s=%s(非数字)" % (k, v))
                        continue
                elif t == "bool":
                    if v not in ("true", "false"):
                        skipped.append("%s=%s(非布尔)" % (k, v))
                        continue
                elif t == "time":
                    # ⚠️ 必须带引号落盘：YAML 1.1 的六十进制会把裸写的 23:00 读成 1380，
                    #    07:00 读成 420 —— 静默时段会悄悄跑偏且极难察觉。
                    if not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", v):
                        skipped.append("%s=%s(应为 时:分，如 23:00)" % (k, v))
                        continue
                    v = '"%s"' % v
                elif t == "enum" and v not in ENUM_CHOICES.get(k, []):
                    skipped.append("%s=%s(不在可选范围)" % (k, v))
                    continue
                section = None
                for kk, ss, _t, _dd in EDITABLE:
                    if kk == k:
                        section = ss
                text = write_config_value(text, k, section, v)
                applied.append("%s=%s" % (k, v))
            if applied:
                # 先校验 YAML 合法性，再备份，最后才落盘——配置改坏等于后台起不来
                try:
                    import yaml as _yaml
                    _yaml.safe_load(text)
                except ImportError:
                    pass
                except Exception as e:
                    return self._json({"ok": False,
                                       "message": "改动会让 YAML 非法，已拒绝写入：%s" % e}, 400)
                bak = CONFIG_FILE.parent / (CONFIG_FILE.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
                try:
                    bak.write_bytes(CONFIG_FILE.read_bytes())
                except Exception:
                    pass
                CONFIG_FILE.write_text(text, encoding="utf-8", newline="\n")
                bak_name = bak.name
            msg = "已写入 %d 项：%s" % (len(applied), ", ".join(applied)) if applied else "没有可写入的项"
            if applied:
                msg += "（原配置已备份为 %s）" % bak_name
            if skipped:
                msg += " ｜跳过：" + ", ".join(skipped)
            msg += "（重启后台后生效）"
            return self._json({"ok": bool(applied), "message": msg})

        return self._json({"ok": False, "message": "not found"}, 404)


def main():
    argv = sys.argv[1:]
    autostart = "--autostart" in argv
    args = [a for a in argv if not a.startswith("--")]
    port = int(args[0]) if args else 8005

    if port_alive(port):
        print("[跳过] 端口 %d 已在监听，管理台本来就在跑：http://127.0.0.1:%d" % (port, port))
        if not autostart:
            input("按回车键退出...")
        return

    print("=" * 64)
    print("小智后台管理台")
    print("  地址:      http://127.0.0.1:%d" % port)
    print("  配置文件:  %s" % CONFIG_FILE)
    print("  服务目录:  %s" % SERVER_DIR)
    print("=" * 64)

    if autostart:
        if port_alive(8000):
            print("[开机自启] 后台服务已在运行，跳过拉起")
        else:
            ok, msg = start_server()
            print("[开机自启] %s" % msg)

    print("提示：本进程由你启动，它拉起的后台服务不会被回收。Ctrl+C 结束管理台。")
    try:
        httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    except OSError as e:
        print("[错误] 无法监听 %d：%s" % (port, e))
        if not autostart:
            input("按回车键退出...")
        return
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n管理台已退出")


if __name__ == "__main__":
    main()
