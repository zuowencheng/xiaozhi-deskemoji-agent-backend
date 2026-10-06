"""探测本机 Ollama 的响应延迟（首字 / 总时长），逐 chunk 打印时间线。

用法:
    python tools/probe_ollama.py [模型名] [提示词]
"""
import json
import sys
import time
import urllib.request

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3:4b"
PROMPT = sys.argv[2] if len(sys.argv) > 2 else "说：你好"
TIMEOUT = float(sys.argv[3]) if len(sys.argv) > 3 else 90.0
URL = "http://localhost:11434/api/chat"

payload = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "/no_think " + PROMPT}],
    "stream": True,
    "think": False,
    "options": {"num_predict": 48, "num_ctx": 2048},
}

print("模型: %s" % MODEL)
print("提问: %s" % PROMPT)
print("超时: %.0f s" % TIMEOUT)
print("-" * 60, flush=True)

req = urllib.request.Request(
    URL,
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
)

t0 = time.time()
t_first = None
n_chunk = 0
buf = []

try:
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        print("[%.2fs] HTTP 响应头已到, status=%s" % (time.time() - t0, r.status), flush=True)
        for raw in r:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            n_chunk += 1
            msg = obj.get("message") or {}
            th = msg.get("thinking") or ""
            ct = msg.get("content") or ""
            if (th or ct) and t_first is None:
                t_first = time.time() - t0
                print("[%.2fs] ★ 首个 token" % t_first, flush=True)
            if th:
                buf.append(th)
            if ct:
                buf.append(ct)
            if n_chunk <= 5 or obj.get("done"):
                print("[%.2fs] chunk#%d %s" % (
                    time.time() - t0, n_chunk,
                    json.dumps(obj.get("message") or {}, ensure_ascii=False)[:120]), flush=True)
            if obj.get("done"):
                break
except Exception as e:
    print("[%.2fs] ✗ 异常: %s: %s" % (time.time() - t0, type(e).__name__, e), flush=True)

total = time.time() - t0
print("-" * 60)
print("[首字] %.2f s" % (t_first if t_first is not None else -1))
print("[总耗时] %.2f s   chunk 数 %d" % (total, n_chunk))
print("[输出] %s" % "".join(buf)[:400])
