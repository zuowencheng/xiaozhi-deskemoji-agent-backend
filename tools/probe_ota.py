# -*- coding: utf-8 -*-
"""
探测自建后台的 OTA 接口回包（模拟设备那一次 POST）。
用 http.client 直连，绕开系统代理（本机跑着 10808/10809 代理，
urllib 会把 127.0.0.1 / 局域网 IP 也塞进代理，看到 502 会误判）。

用法：
    python tools/probe_ota.py                 # 默认打 192.168.3.139:8003
    python tools/probe_ota.py 127.0.0.1 8003  # 换目标
"""
import http.client
import json
import socket
import sys

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.3.139"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8003
PATH = "/xiaozhi/ota/"

MAC = "84:c7:bb:58:17:44"
UUID = "40529486-f26b-417c-a18b-d2fb34475110"
BOARD = "esp32-s3n16r8-emoji"
APP_VERSION = "2.0.1"

body = json.dumps(
    {
        "application": {
            "name": "xiaozhi",
            "version": APP_VERSION,
            "idf_version": "v5.5.4",
            "elf_sha256": "5be30c4d0",
        },
        "board": {"type": BOARD, "mac": MAC},
        "chip_model_name": "esp32s3",
    },
    ensure_ascii=False,
)

# ---- 1. TCP 可达性 ----
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(3)
rc = s.connect_ex((HOST, PORT))
s.close()
print("[tcp] %s:%d -> %s" % (HOST, PORT, "OPEN" if rc == 0 else "CLOSED(err=%d)" % rc))
if rc != 0:
    print("  服务不在监听 —— 这才是设备'更新失败'的直接原因")
    sys.exit(1)

# ---- 2. 打 OTA 接口 ----
headers = {
    "Content-Type": "application/json",
    "Device-Id": MAC,
    "Client-Id": UUID,
    "User-Agent": "xiaozhi-esp32/%s" % APP_VERSION,
    "Accept-Language": "zh-CN",
}
conn = http.client.HTTPConnection(HOST, PORT, timeout=8)
conn.request("POST", PATH, body=body.encode("utf-8"), headers=headers)
r = conn.getresponse()
raw = r.read().decode("utf-8", "replace")
conn.close()

print("[http] POST %s -> %d %s" % (PATH, r.status, r.reason))
try:
    obj = json.loads(raw)
    print("[json] 解析成功，顶层键: %s" % list(obj.keys()))
    print(json.dumps(obj, ensure_ascii=False, indent=2)[:2500])
    fw = obj.get("firmware")
    if fw:
        print("\n[!] 服务器下发了固件（设备会去下载）: %s" % fw.get("url"))
    else:
        print("\n[ok] 服务器未下发固件（firmware 为空）—— 设备会认为已是最新，正常继续")
    print("[ws] websocket: %s" % obj.get("websocket"))
except Exception as e:
    print("[json] 解析失败: %s" % e)
    print(raw[:2000])
