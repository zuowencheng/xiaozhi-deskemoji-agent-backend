# -*- coding: utf-8 -*-
"""
验证「执行器步数预算 + 能力边界硬拦截 + 遥测」三件事**真的生效**。

⚠️ 铁律 4d：py_compile 过关什么都不证明。这里全部用**真实历史数据**跑：
  - 真实失败任务原文（来自 小智任务中心.md 的 7 条失败单）
  - 真实配置文件（data/.config.yaml）
  - 真实任务单格式（造临时文件走 _rewrite_status）
"""
import sys, os, re, tempfile
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "server" / "xiaozhi-server"
sys.path.insert(0, str(SERVER))
os.chdir(SERVER)

from core import pc_agent  # noqa

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  -> " + str(extra)) if extra else ""))


print("\n=== ① 预算与模型：执行器要走便宜模型 ===")
conf = pc_agent._load_llm_conf()
print("   实际读到:", {k: conf[k] for k in ("model", "max_tokens", "max_rounds", "max_task_sec")})
check("执行器模型 = deepseek-v4-flash（不是主对话那个贵的）",
      conf["model"] == "deepseek-v4-flash", conf["model"])
check("max_tokens 已从 1200 放宽到 4096", conf["max_tokens"] == 4096, conf["max_tokens"])
check("max_rounds 已从写死 14 提到 20", conf["max_rounds"] == 20, conf["max_rounds"])
check("全局墙钟 max_task_sec = 240（原来没有）", conf["max_task_sec"] == 240, conf["max_task_sec"])
check("api_key 有值（便宜模型那套凭据齐全）", bool(conf["api_key"]))

print("\n=== ② 能力边界硬拦截：真实的 B 类失败任务 ===")
# 真实任务单原文（取自 小智任务中心.md）
REAL_TASKS = [
    # (任务原文, 期望是否被拒绝, 说明)
    ("通过手机上的workbody应用，帮主人打开美团外卖App，方便他点餐。", True,
     "B类真凶：20261003-110339，物理上够不到手机"),
    ("用手机打开美团外卖", True, "同上，简短说法"),
    ("帮我在 iPad 上查一下邮件", True, "够不到平板"),
    ("去公司电脑上把那个文件拷过来", True, "够不到别的机器"),
    ("在电脑上打开腾讯的 QQ 应用程序", False, "C类超时任务：本机能力范围内，不该被误拒"),
    ("关闭小智语音助手说话前的开场提示音（BB提示音），或将其音量调至最低", False,
     "A类排查型：本机可查，不该被误拒"),
    ("主人说他的电脑外接了一个显示器，但现在外接显示器上没有画面显示，请检查显示设置", False,
     "A类排查型：本机可查"),
    ("打开微信", False, "★ 防误伤：电脑版微信是合法任务，绝不能因为含微信二字被拒"),
    ("在 WorkBuddy 里新建一个任务", False, "A类跨系统：本机可做"),
]

for text, should_refuse, why in REAL_TASKS:
    got = pc_agent._refuse_if_impossible(text)
    refused = got is not None
    ok = (refused == should_refuse)
    mark = "拒绝" if refused else "放行"
    check(f"{mark} <- {text[:26]}", ok, f"期望{'拒绝' if should_refuse else '放行'} | {why}")

print("\n=== ③ 遥测：steps/ms 真的落盘了吗 ===")
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "task-test001.md"
    p.write_text(
        "# 任务 test001\n\n- 状态：pending\n- 来源：语音（小智 deskemoji）\n\n"
        "## 任务内容\n\n测试\n\n## 执行结果\n\n（待填）\n",
        encoding="utf-8")
    pc_agent._rewrite_status(p, "failed", "跑了 240 秒还没干完", 7, 241000)
    txt = p.read_text(encoding="utf-8")
    has_steps = bool(re.search(r"^-\s*步数：7\s*$", txt, re.M))
    has_ms = bool(re.search(r"^-\s*耗时：241000ms\s*$", txt, re.M))
    has_status = "- 状态：failed" in txt
    check("任务单写入「步数：7」", has_steps)
    check("任务单写入「耗时：241000ms」", has_ms)
    check("状态正确改为 failed", has_status)
    check("执行结果段被正确替换", "跑了 240 秒还没干完" in txt)
    # 幂等：再写一次不应出现两行步数
    pc_agent._rewrite_status(p, "done", "好了", 9, 1000)
    txt2 = p.read_text(encoding="utf-8")
    check("重复写入不产生两行步数（幂等）",
          len(re.findall(r"^-\s*步数：", txt2, re.M)) == 1,
          re.findall(r"^-\s*步数：.*$", txt2, re.M))
    check("第二次写入覆盖为 9", "- 步数：9" in txt2)

print("\n=== ④ 墙钟：真的会到点停吗（用假 LLM 走真实循环）===")
import core.pc_agent as pa  # noqa


class _FakeMsg:
    def __init__(self): self.content = ""; self.tool_calls = None


class _FakeResp:
    def __init__(self): self.choices = [type("C", (), {"message": _FakeMsg()})()]


class _FakeCompletions:
    """每轮慢速返回一个「无效工具调用」，让循环一直跑 —— 好让墙钟有机会叫停。

    ⚠️ 第一版我让它直接返回「无工具调用」，结果第一轮就正常结束了，
       根本没跑到墙钟 —— 测试写错比代码写错更坑，这里特意改成会持续循环的。
    """

    def __init__(self): self.n = 0

    def create(self, **kw):
        import time as _t
        self.n += 1
        _t.sleep(0.6)
        tc = type("TC", (), {})()
        tc.id = "1"
        tc.function = type("F", (), {"name": "no_such_tool", "arguments": "{}"})()
        m = _FakeMsg(); m.tool_calls = [tc]
        return type("R", (), {"choices": [type("C", (), {"message": m})()]})()


class _FakeClient:
    def __init__(self, **kw):
        self.chat = type("Ch", (), {"completions": _FakeCompletions()})()


# 把墙钟临时压到 1 秒，看它会不会停
orig = pa._load_llm_conf
pa._load_llm_conf = lambda: {
    "base_url": "x", "api_key": "k", "model": "m", "temperature": 0.3,
    "max_tokens": 4096, "max_rounds": 999999, "max_task_sec": 1,
}
import openai  # noqa
pa.OpenAI = _FakeClient        # run_agent 内部 `from openai import OpenAI`… 见下
try:
    # run_agent 里是 `from openai import OpenAI`，因此要 patch openai 模块属性
    openai.OpenAI = _FakeClient
    out = pa.run_agent("测试墙钟")
    check("墙钟生效：到点返回 ok=False", out["ok"] is False, out.get("reason"))
    check("失败原因标记为 timeout", out.get("reason") == "timeout", out.get("reason"))
    check("文案里带了真实秒数（不再是一刀切那句）",
          "秒" in out["result"] and "步骤太多" not in out["result"], out["result"][:60])
finally:
    pa._load_llm_conf = orig

print("\n=== ⑤ 步数耗尽文案：不再是那句一刀切 ===")
pa._load_llm_conf = lambda: {
    "base_url": "x", "api_key": "k", "model": "m", "temperature": 0.3,
    "max_tokens": 4096, "max_rounds": 2, "max_task_sec": 9999,
}


class _LoopCompletions:
    """每轮都调一个不存在的工具 → 快速耗尽步数。"""

    def create(self, **kw):
        tc = type("TC", (), {})()
        tc.id = "1"
        tc.function = type("F", (), {"name": "no_such_tool", "arguments": "{}"})()
        m = _FakeMsg(); m.tool_calls = [tc]
        return type("R", (), {"choices": [type("C", (), {"message": m})()]})()


openai.OpenAI = lambda **kw: type("Cl", (), {
    "chat": type("Ch", (), {"completions": _LoopCompletions()})()})()
try:
    out = pa.run_agent("耗尽步数测试")
    check("步数耗尽返回 ok=False", out["ok"] is False)
    check("原一刀切文案「建议拆成小任务再说一遍」已消失",
          "建议拆成小任务再说一遍" not in out["result"])
    check("文案带上真实步数", "步" in out["result"], out["result"][:70])
    check("reason = steps_exhausted", out.get("reason") == "steps_exhausted")
finally:
    pa._load_llm_conf = orig

print("\n" + "=" * 62)
print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
if FAIL:
    print("❌ 失败项：")
    for f in FAIL:
        print("   -", f)
    sys.exit(1)
print("✅ 全部通过")
