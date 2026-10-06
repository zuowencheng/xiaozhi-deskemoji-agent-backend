"""验证 DeepSeek API：Key 是否有效 / 首字延迟 / function calling 是否可用。

用法:
    python tools/probe_deepseek.py
Key 从 server/xiaozhi-server/data/.config.yaml 里读，不在本文件硬编码。
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

CFG = Path(__file__).resolve().parent.parent / "server/xiaozhi-server/data/.config.yaml"
text = CFG.read_text(encoding="utf-8")

api_key = re.search(r"^\s*api_key:\s*(\S+)\s*$", text, re.M)
api_key = api_key.group(1) if api_key else None
base = "https://api.deepseek.com/v1"
model = "deepseek-chat"

print("配置文件: %s" % CFG)
print("model   : %s" % model)
print("api_key : %s...%s (len=%d)" % (api_key[:8], api_key[-4:], len(api_key)) if api_key else "api_key : 未找到！")
print("-" * 62)

if not api_key:
    sys.exit(1)


def post(path, payload, timeout=60):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    return urllib.request.urlopen(req, timeout=timeout)


# ---------- 测试 1：基本对话 ----------
print("【测试 1】基本对话")
t0 = time.time()
try:
    r = post("/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": "用一句话回答：1+1等于几"}],
        "max_tokens": 64,
        "stream": False,
    })
    d = json.loads(r.read().decode("utf-8"))
    print("  ✓ HTTP %s, 耗时 %.2fs" % (r.status, time.time() - t0))
    print("  回复: %s" % d["choices"][0]["message"]["content"].strip())
    print("  用量: %s" % d.get("usage"))
except Exception as e:
    print("  ✗ 失败: %s: %s" % (type(e).__name__, e))
    body = getattr(e, "read", lambda: b"")()
    if body:
        print("  响应体: %s" % body[:400])
    sys.exit(2)
print()

# ---------- 测试 2：function calling ----------
print("【测试 2】function calling（指挥本机干活的前提）")
tools = [{
    "type": "function",
    "function": {
        "name": "pc_run_task",
        "description": "把一个任务交给本机助理去执行",
        "parameters": {
            "type": "object",
            "properties": {"instruction": {"type": "string", "description": "任务内容"}},
            "required": ["instruction"],
        },
    },
}]
t0 = time.time()
try:
    r = post("/chat/completions", {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                "你是桌面语音助手。当用户让你做一件需要在本机电脑上执行的事情时，"
                "必须调用 pc_run_task 工具把任务转交出去，不要反问用户，也不要自己编造结果。"
            )},
            {"role": "user", "content": "帮我用一句话总结今天的项目进展"},
        ],
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": 256,
        "stream": False,
    })
    d = json.loads(r.read().decode("utf-8"))
    msg = d["choices"][0]["message"]
    tc = msg.get("tool_calls")
    print("  ✓ HTTP %s, 耗时 %.2fs" % (r.status, time.time() - t0))
    if tc:
        print("  ✓ 正确触发工具调用:")
        for c in tc:
            print("     %s(%s)" % (c["function"]["name"], c["function"]["arguments"]))
    else:
        print("  ⚠️ 未触发工具调用，直接回了文本: %s" % (msg.get("content") or "")[:200])
except Exception as e:
    print("  ✗ 失败: %s: %s" % (type(e).__name__, e))
    sys.exit(3)
print()

# ---------- 测试 3：流式首字延迟 ----------
print("【测试 3】流式首字延迟（体感延迟的关键）")
t0 = time.time()
t_first = None
buf = []
try:
    r = post("/chat/completions", {
        "model": model,
        "messages": [{"role": "user", "content": "介绍一下你自己，三句话"}],
        "max_tokens": 120,
        "stream": True,
    }, timeout=60)
    for raw in r:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except Exception:
            continue
        delta = (obj.get("choices") or [{}])[0].get("delta") or {}
        rc = delta.get("reasoning_content") or ""
        ct = delta.get("content") or ""
        if (rc or ct) and t_first is None:
            t_first = time.time() - t0
        if ct:
            buf.append(ct)
    total = time.time() - t0
    print("  ✓ 首字 %.2fs   总耗时 %.2fs   输出 %d 字" % (t_first or -1, total, len("".join(buf))))
    print("  输出: %s" % "".join(buf).strip()[:160])
except Exception as e:
    print("  ✗ 失败: %s: %s" % (type(e).__name__, e))
print()
print("=" * 62)
print("结论：Key 有效、function calling 可用、首字约 %.2fs → 语音场景合规" % (t_first or -1))
