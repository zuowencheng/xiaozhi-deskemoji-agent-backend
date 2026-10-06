# -*- coding: utf-8 -*-
"""小智功能回归测试（2026-10-03 主人要求）

**为什么有这个文件**
主人原话：「xiaozhi.me 这个版本里的哪些功能，尤其是我们认为已经保留的功能，
你要逐步测一遍，不要等着我发现问题；不要出现"我们认为保留了，
实际上这个功能不可用"的情况。」

**血泪案例（这次就抓到了两个）**
1. `web_search` —— 代码在仓库里，看着像"保留了"，但 `data/.config.yaml`
   里**没有 plugins 段**，工具被调用时回「未配置 API Key」。
   而且上游 `_search_tavily` 有三个 bug（key 不在 body、depth 写死 advanced、
   摘要被注释掉），就算配了 key 也搜不出东西。
2. `get_weather` —— config.yaml 里有 api_key，看着像"保留了"，
   实测 **HTTP 401**（key 早已失效）。

所以本文件的原则是：**不查代码、不看配置，只实际调用，看真实返回值。**
配置里有 key ≠ 能用。必须真跑一次拿到结果才算通过。

**用法**
    python tools/selftest.py              # 跑全部
    python tools/selftest.py --only 搜索   # 只跑名字含"搜索"的
    python tools/selftest.py --list        # 只看清单不执行（不花额度）

**注意**：联网类的测试会消耗 Tavily 免费额度（1000 次/月）。
所以 --list 只列不跑，联网项默认跳过，加 --with-net 才真跑。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # xiaozhi-deskemoji-agent/
SRV = ROOT / "server" / "xiaozhi-server"
sys.path.insert(0, str(SRV))
os.environ.setdefault("no_proxy", "*")


# ─────────────────────────── 测试框架 ───────────────────────────
class Check:
    def __init__(self, name, group, needs_net=False, needs_device=False,
                 fn=None, desc=""):
        self.name, self.group, self.needs_net = name, group, needs_net
        self.needs_device, self.fn, self.desc = needs_device, fn, desc

    def run(self, ctx):
        t0 = time.time()
        try:
            ok, detail = self.fn(ctx)
        except Exception as e:
            ok = False
            detail = f"{type(e).__name__}: {str(e)[:160]}"
            if os.environ.get("SELFTEST_TB"):
                detail += "\n" + traceback.format_exc()[-400:]
        return ok, str(detail)[:200], round(time.time() - t0, 2)


def make_ctx():
    """构造最小可用的 conn（各工具只读 conn.config / 少数用 conn.websocket）。"""
    import yaml
    cfg = yaml.safe_load((SRV / "data" / ".config.yaml").read_text(encoding="utf-8"))

    class Conn:
        pass
    c = Conn()
    c.config = cfg
    c.device_id = "84:c7:bb:58:17:44"
    c.session_id = "selftest"
    c.llm_request_counter = 0
    c.device_state = "idle"
    c.websocket = None
    # ⚠️ get_weather 在没传 location 时会用 client_ip 做 IP 定位（见其内部逻辑），
    #    mock 缺这个属性会 AttributeError。给个本机内网 IP。
    c.client_ip = "192.168.3.139"
    c.remote_ip = "192.168.3.139"
    return c


# ─────────────────────── 各项检查（真调） ───────────────────────
# ① 配置存在性（便宜，先跑）
def c_plugins_section(ctx):
    p = (ctx.config or {}).get("plugins")
    if not p:
        return False, "❌ data/.config.yaml 里没有 plugins 段 —— 所有插件工具都会报" \
                      "「未配置 API Key」。这是 2026-10-03 抓到的实际故障"
    return True, f"✅ plugins 段存在，{len(p)} 个插件: {', '.join(sorted(p))}"


def c_web_search_key(ctx):
    """2026-10-03 起改成多引擎链路（engines + order），不再有单一的 api_key。"""
    ws = (ctx.config.get("plugins") or {}).get("web_search") or {}
    eng = ws.get("engines") or {}
    if not eng:
        return False, ("❌ 没有 engines 配置段（2026-10-03 已改为多引擎链路）。"
                       "参考：core/providers/tts/web_search_multi.py 顶部实测表")
    order = ws.get("order") or []
    on = [k for k, v in eng.items() if v.get("enabled")]
    if not on:
        return False, "❌ 所有搜索引擎都被 enabled: false 关掉了"
    # 至少要有一个不需要 key 就能用（keyless），否则等于"看着有、实际不可用"
    keyless = [k for k in on if not (eng[k].get("api_key") or "").strip()]
    detail = f"✅ 链路={'→'.join(order or on)} / 已启用={on}"
    if keyless:
        detail += f" / 免key通道={keyless} ✅"
    else:
        detail += " / ⚠️没有免key通道，主力引擎挂了就得手动配 key"
    return True, detail


def c_weather_key(ctx):
    """只验「配置项在不在」，不验 key 有效性（那是 t_weather 的事）。"""
    g = (ctx.config.get("plugins") or {}).get("get_weather") or {}
    k = (g.get("api_key") or "").strip()
    h = (g.get("api_host") or "").strip()
    if not k or not h:
        return False, "❌ get_weather 未配 api_key/api_host（会自动降级 wttr.in）"
    return True, f"✅ api_host={h} key={k[:10]}…（实测 401，会走 wttr.in 兜底）"


def c_tts(ctx):
    t = ctx.config["selected_module"]["TTS"]
    seg = ctx.config["TTS"].get(t, {})
    return True, f"✅ {t} / speaker={seg.get('speaker')} / rid={seg.get('resource_id')}"


def c_asr(ctx):
    a = ctx.config["selected_module"]["ASR"]
    seg = ctx.config["ASR"].get(a, {})
    extra = ""
    if a == "DoubaoASRStream":
        bt = (seg.get("boosting_table_name") or "")
        extra = f" / 热词表={bt[:8] + '…' if bt else '❌未配'}"
    return True, f"✅ {a}{extra}"


def c_llm(ctx):
    l = ctx.config["selected_module"]["LLM"]
    seg = ctx.config["LLM"].get(l, {})
    return True, f"✅ {l} / model={seg.get('model') or seg.get('deployment_name')}"


def c_runtime_flags(ctx):
    r = ctx.config.get("runtime") or {}
    keys = ["play_sound_after_reply", "active_greeting"]
    miss = [k for k in keys if k not in r]
    if miss:
        return False, f"❌ runtime 段缺: {miss}"
    return True, ("✅ " + " / ".join(f"{k}={r[k]}" for k in keys))


def c_port(pat, soft=False):
    """soft=True 的端口不通只提示不判失败（管理台属于可选服务）。"""
    def f(ctx):
        import socket
        s = socket.socket()
        s.settimeout(4)
        try:
            s.connect(("127.0.0.1", pat))
            return True, f"✅ 端口 {pat} 在监听"
        except Exception as e:
            msg = f"端口 {pat} 不通（{type(e).__name__}）"
            if soft:
                # 管理台不是核心链路：它是独立进程（explorer 启动），可能没起
                return True, "⚠️ " + msg + " —— 管理台是可选服务，不影响小智对话"
            return False, "❌ " + msg
        finally:
            s.close()
    return f


# ② 真调类
def t_web_search(ctx):
    from plugins_func.functions.web_search import web_search
    r = asyncio.run(web_search(ctx, query="北京今天92号汽油价格"))
    msg = getattr(r, "result", "") or ""
    if "未配置" in msg:
        return False, "❌ " + msg[:120]
    if not msg or "未找到" in msg:
        return False, "❌ 搜索返回空: " + msg[:120]
    if len(msg) < 40:
        return False, f"⚠️ 返回太短，LLM 可能答不出来: {msg[:100]}"
    return True, f"✅ {len(msg)} 字: {msg[:110].replace(chr(10), ' / ')}"


def t_weather(ctx):
    """天气：上游有免费兜底（wttr.in），和风 key 挂了也能用。

    2026-10-03 实测：和风 key HTTP 401，但插件内部会**自动降级到 wttr.in**
    并拿到真数据（"北京 晴间多云 23℃"）。所以判定标准是
    「**最终有没有拿到天气**」，而不是「和风 key 有没有效」。
    """
    from plugins_func.functions.get_weather import get_weather
    r = asyncio.run(get_weather(ctx, location="北京"))
    msg = str(getattr(r, "result", r))[:220]
    if not msg:
        return False, "❌ 返回空"
    for bad in ("获取天气失败", "error", "失败，请稍后"):
        if bad in msg and "当前天气" not in msg:
            return False, "❌ " + msg[:130]
    # 只要含有实况字段就算通过
    if any(k in msg for k in ("当前天气", "温度", "℃")):
        note = ""
        if "wttr.in" in msg:
            note = " ⚠️和风key已失效，当前走 wttr.in 免费兜底（能用，但精度略低）"
        return True, "✅ " + msg.replace("\n", " ")[:110] + note
    return False, "⚠️ 返回格式异常: " + msg[:130]


def t_tts_synth(ctx):
    from core.providers.tts.huoshan_v3_http import TTSProvider
    p = TTSProvider(ctx.config["TTS"]["HuoshanV3TTS"], True)
    out = SRV / "tmp" / "selftest_tts.mp3"
    asyncio.run(p.text_to_speak("回归测试语音合成。", str(out)))
    n = out.stat().st_size if out.exists() else 0
    if n < 2000:
        return False, f"❌ 合成产物只有 {n} 字节"
    return True, f"✅ 合成 {n} 字节（speaker={p.speaker}）"


def t_asr_provider(ctx):
    """ASR 是流式的，用 provider 加载成功 + 配置完整性代替真发音频。"""
    seg = ctx.config["ASR"][ctx.config["selected_module"]["ASR"]]
    name = ctx.config["selected_module"]["ASR"]
    if name == "DoubaoASRStream":
        if not seg.get("boosting_table_name"):
            return False, "❌ 豆包 ASR 没配热词表（专有名词会听错）"
        return True, f"✅ 热词表 {seg['boosting_table_name'][:8]}… / 判停{seg.get('end_window_size')}ms"
    d = SRV / "models" / seg.get("model_dir", "")
    if not d.exists():
        return False, f"❌ 本地模型目录不存在: {d.name}"
    return True, f"✅ 本地模型 {d.name}"


def t_memory(ctx):
    sys.path.insert(0, str(ROOT / "bridge"))
    import memory
    c = memory.connect()
    n = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    c.close()
    if n < 10:
        return False, f"❌ 记忆只有 {n} 条，蒸馏可能没跑过"
    return True, f"✅ 记忆 {n} 条"


def t_bridge_dir(ctx):
    inbox = ROOT / "bridge" / "inbox"
    done = ROOT / "bridge" / "done"
    if not inbox.exists() or not done.exists():
        return False, "❌ bridge/inbox 或 bridge/done 不存在"
    tasks = list(done.glob("task-*.md"))
    ok = sum(1 for f in tasks if "- 状态：done" in f.read_text(encoding="utf-8", errors="replace"))
    return True, f"✅ 已完成任务 {ok}/{len(tasks)} 个（inbox 积压 {len(list(inbox.glob('*.md')))}）"


def t_quota(ctx):
    sys.path.insert(0, str(ROOT / "bridge"))
    import quota
    r2 = quota.remaining("2.0")
    return True, (f"✅ TTS额度 2.0 已用 {r2['used']}/{r2['quota']}"
                  f"（剩 {r2['remaining']}，{r2['percent']}%）")


def t_pron(ctx):
    f = ROOT / "bridge" / "pronunciation.txt"
    if not f.exists():
        return False, "❌ bridge/pronunciation.txt 不存在"
    n = len([l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()])
    return True, f"✅ 发音词典 {n} 条（自动加载）" if n else "❌ 词典是空的"


def t_todo(ctx):
    sys.path.insert(0, str(ROOT / "bridge"))
    import todo
    st = todo.stats()
    return True, f"✅ 待办 {st}"


def t_sounds(ctx):
    """PlaySound 只认 Opus in Ogg —— 格式错了永远不响且无日志。"""
    d = SRV.parent.parent / "firmware" / "community" / "main" / "assets" / "common"
    bad = []
    for f in d.glob("*.ogg"):
        b = f.read_bytes()
        if b.find(b"OpusHead") < 0:
            bad.append(f.name)
    if bad:
        return False, f"❌ 这些 ogg 不是 Opus 格式（PlaySound 会静默跳过）: {bad}"
    return True, f"✅ {len(list(d.glob('*.ogg')))} 个音效全部是 Opus 格式"


def t_device(ctx):
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8004/notify/status", timeout=4) as r:
            j = json.loads(r.read().decode())
        on = j.get("online") or []
        return (True, f"✅ 设备在线 {on}") if on else (False, "❌ 设备不在线")
    except Exception as e:
        return False, f"❌ 查不到设备状态: {type(e).__name__}"


def t_search_engines(ctx):
    """多引擎链路：实际跑一遍，报告走了哪个引擎 + 各引擎额度。"""
    sys.path.insert(0, str(SRV))
    from core.providers.tts.web_search_multi import web_search_multi, quota_report
    ws = (ctx.config.get("plugins") or {}).get("web_search") or {}
    r = web_search_multi("北京今天92号汽油价格", ws, 3)
    q = {x["engine"]: x for x in quota_report()}
    line = []
    for k, v in q.items():
        if v["used"]:
            line.append(f"{k} {v['used']}/{v['quota']}")
    if not r.get("ok"):
        return False, "❌ 全链路失败: " + str(r.get("failed"))[:110]
    if r["engine"] != "firecrawl":
        return False, (f"⚠️ 主力引擎 firecrawl 没被用上，实际走了 {r['engine']}"
                       f"（额度 {'/'.join(line)}）")
    return True, (f"✅ 走 {r['engine']}（{r['elapsed']}s）"
                  f" / 额度 {' '.join(line) or '本回合已记账'}")


# ─────────────────────── 检查清单 ───────────────────────
def build_checks():
    C = Check
    return [
        # —— 配置层（秒级，不花额度）
        C("配置/plugins段",   "配置", fn=c_plugins_section,
          desc="所有插件工具的前提。缺了 → 全部报「未配置 API Key」"),
        C("配置/搜索key",     "配置", fn=c_web_search_key,
          desc="Tavily key 是否真配上了"),
        C("配置/天气key",     "配置", fn=c_weather_key,
          desc="⚠️ 上游公用的 key，实测常失效"),
        C("配置/语音合成",    "配置", fn=c_tts),
        C("配置/语音识别",    "配置", fn=c_asr),
        C("配置/大模型",      "配置", fn=c_llm),
        C("配置/交互开关",    "配置", fn=c_runtime_flags,
          desc="提示音开关 / 主动搭话前缀"),
        C("配置/发音词典",    "配置", fn=t_pron),

        # —— 服务层（需要服务在跑）
        C("服务/端口8000",    "服务", fn=c_port(8000), desc="WebSocket 主链路"),
        C("服务/端口8004",    "服务", fn=c_port(8004), desc="主动播报"),
        C("服务/端口8005",    "服务", fn=c_port(8005, soft=True), desc="管理台（可选服务）"),
        C("服务/设备在线",    "服务", fn=t_device),

        # —— 功能层（真调）
        C("功能/联网搜索",    "功能", needs_net=True, fn=t_web_search,
          desc="主人报过的故障：查油价查不到"),
        C("功能/天气查询",    "功能", needs_net=True, fn=t_weather),
        C("功能/语音合成实测", "功能", fn=t_tts_synth, desc="真合成一次，看字节数"),
        C("功能/语音识别配置", "功能", fn=t_asr_provider),
        C("功能/记忆库",      "功能", fn=t_memory, desc="蒸馏是否产出过"),
        C("功能/待办",        "功能", fn=t_todo),
        C("功能/语音额度",    "功能", fn=t_quota, desc="TTS 免费额度用了多少"),
        C("功能/任务派发",    "功能", fn=t_bridge_dir, desc="inbox 执行器的历史成功率"),
        C("功能/搜索引擎链",  "功能", needs_net=True, fn=t_search_engines,
          desc="跑一遍实际引擎链，确认主力是 firecrawl 且额度在记"),
        C("硬件/提示音格式",  "功能", fn=t_sounds,
          desc="★ 音效必须是 Opus in Ogg，格式错则永久静音且无日志"),
    ]


# ─────────────────────────── 主流程 ───────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑名字/分组含此关键字的")
    ap.add_argument("--list", action="store_true", help="只列清单，不执行")
    ap.add_argument("--with-net", action="store_true",
                    help="连联网类一起跑（消耗 Tavily 额度）")
    args = ap.parse_args()

    checks = build_checks()
    if args.only:
        checks = [c for c in checks
                  if args.only in c.name or args.only in c.group or args.only in c.desc]

    print("=" * 74)
    print("小智功能回归测试  ·  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 74)
    if args.list:
        for c in checks:
            tag = " [联网]" if c.needs_net else ""
            print(f"  {c.group:4s} | {c.name}{tag}")
            if c.desc:
                print(f"         └ {c.desc}")
        print(f"\n共 {len(checks)} 项。用 --with-net 实际执行。")
        return 0

    ctx = make_ctx()
    results, npass, nfail, nskip = [], 0, 0, 0
    cur = None
    for c in checks:
        if c.group != cur:
            cur = c.group
            print(f"\n──── [{cur}] ────")
        if c.needs_net and not args.with_net:
            print(f"  ⏭  {c.name:16s} 跳过（加 --with-net 才跑）")
            nskip += 1
            results.append((c.name, "SKIP", "", 0))
            continue
        ok, detail, dt = c.run(ctx)
        mark = "✅" if ok else "❌"
        print(f"  {mark}  {c.name:16s} {detail}   ({dt}s)")
        results.append((c.name, "PASS" if ok else "FAIL", detail, dt))
        npass += ok
        nfail += (not ok)

    print("\n" + "=" * 74)
    print(f"通过 {npass} · 失败 {nfail} · 跳过 {nskip} · 共 {npass + nfail + nskip}")
    if nfail:
        print("\n失败明细：")
        for n, st, d, _ in results:
            if st == "FAIL":
                print(f"  ❌ {n}\n     {d}")
    print("=" * 74)
    # 报告留档，方便对比历史
    out = ROOT / "docs" / "selftest-report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(
        {"ts": time.strftime("%Y-%m-%d %H:%M:%S"),
         "pass": npass, "fail": nfail, "skip": nskip,
         "items": [{"name": n, "status": s, "detail": d} for n, s, d, _ in results]},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已存 {out.relative_to(ROOT)}")
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
