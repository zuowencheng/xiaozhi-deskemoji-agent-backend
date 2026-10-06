# -*- coding: utf-8 -*-
"""
xiaozhi_say · 让小智开口说话（一句话命令行）
===========================================

用途
----
任何本机进程/脚本/定时任务，想让桌上的小智说一句话，就调它：

    python tools/xiaozhi_say.py 快递到了，记得拿
    python tools/xiaozhi_say.py "你让我整理的文件弄好了" --tag done
    python tools/xiaozhi_say.py --status          # 看看设备在不在、队列攒了几句

为什么需要它
------------
"主动找我"这条链路的最后一厘米。后台 8004 端口已经能推话给设备，
但调用方（我、定时任务、别的脚本）需要一个**零依赖、一行搞定**的入口，
不必每次手搓 HTTP。有了它：

  · WorkBuddy 干完一个任务（不管是不是小智派的）→ 调一次 → 小智主动开口
  · 定时任务收尾 → 调一次 → 播报结果
  · 任何脚本的 try/except 出口 → 调一次 → 出错也能喊

设备离线怎么办
--------------
后台会把话排队（data/notify_queue.json），设备一上线自动补播，不丢。
所以调用方**不用管重试**，喊就完了。

返回码
------
    0 已播 / 已入队      1 参数错        2 后台不可达
"""

import json
import sys
import urllib.error
import urllib.request

HOST = "127.0.0.1"
PORT = 8004


def _post(path: str, payload: dict = None, timeout: int = 10):
    url = f"http://{HOST}:{PORT}{path}"
    if payload is None:
        req = urllib.request.Request(url, method="GET")
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    # 本机代理（10808/10809）会拦 127.0.0.1 → 必须显式绕过
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout).read().decode("utf-8", "replace")


def say(text: str, tag: str = "") -> bool:
    try:
        r = json.loads(_post("/notify", {"text": text, "tag": tag}))
    except urllib.error.URLError as e:
        print(f"[xiaozhi_say] 后台不可达（8004 没起？）: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[xiaozhi_say] 失败: {e}", file=sys.stderr)
        return False
    if r.get("queued"):
        print(f"[xiaozhi_say] 设备离线，已排队等补播：{text[:40]}")
    else:
        print(f"[xiaozhi_say] 已播报 -> {r.get('device_id')}：{text[:40]}")
    return True


def status():
    try:
        print(_post("/notify/status"))
    except Exception as e:
        print(f"[xiaozhi_say] 后台不可达: {e}", file=sys.stderr)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] in ("--status", "-s"):
        status()
        return 0
    if args[0] in ("--help", "-h"):
        print(__doc__)
        return 0

    tag = ""
    if "--tag" in args:
        i = args.index("--tag")
        if i + 1 < len(args):
            tag = args[i + 1]
            args = args[:i] + args[i + 2:]
        else:
            args = args[:i]
    text = " ".join(args).strip()
    if not text:
        print("[xiaozhi_say] 没给我要念的话", file=sys.stderr)
        return 1
    return 0 if say(text, tag) else 2


if __name__ == "__main__":
    sys.exit(main())
