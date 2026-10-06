# -*- coding: utf-8 -*-
"""
设备连接稳定性观察器（只读，不碰串口！）
------------------------------------------------------------
⚠️ 血泪教训（见 MEMORY.md 约定 10）：
   `tools/read_serial.py` 打开/关闭串口会触发 `rst:0x15 USB_UART_CHIP_RESET`。
   **判断设备稳不稳，绝对不许碰串口**，也不要碰 setDTR/setRTS。
   唯一正确判据 = 服务端日志里 `OTA请求设备ID` 的计数（OTA 只在设备开机时发生）。
     - 计数不增长 → 设备稳
     - 计数增长   → 设备在重启（"频繁掉线"其实是频繁重启）

用法：
    python tools/watch_device.py --minutes 15
    python tools/watch_device.py --minutes 15 --json-out tmp/watch.json
"""
import argparse
import json
import os
import re
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "server", "_logs", "server.out.log")


def count(pattern, path=LOG):
    """统计日志里匹配 pattern 的行数（文件按二进制读，避开 GBK 解码炸掉）。"""
    n = 0
    try:
        with open(path, "rb") as f:
            for line in f:
                if re.search(pattern.encode(), line):
                    n += 1
    except FileNotFoundError:
        return -1
    return n


def snapshot():
    return {
        "time": datetime.now().strftime("%H:%M:%S"),
        "ota": count(r"OTA请求设备ID"),        # 设备开机次数（核心判据）
        "online": count(r"设备上线"),           # WS 连接建立次数
        "offline": count(r"客户端断开连接"),    # WS 断开次数
        "server_started": count(r"启动服务器成功|Server startup|Uvicorn running"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=10, help="观察多少分钟")
    ap.add_argument("--interval", type=int, default=30, help="采样间隔（秒）")
    ap.add_argument("--json-out", default="", help="把结果写成 json")
    args = ap.parse_args()

    base = snapshot()
    print("=== 基线 ===")
    print("  时间      : %s" % base["time"])
    print("  OTA(开机) : %d" % base["ota"])
    print("  上线      : %d" % base["online"])
    print("  断开      : %d" % base["offline"])
    print("  观察 %d 分钟，每 %d 秒采样一次（全程不碰串口）\n" % (args.minutes, args.interval))

    samples = [base]
    rounds = max(1, int(args.minutes * 60 / args.interval))
    for i in range(1, rounds + 1):
        time.sleep(args.interval)
        s = snapshot()
        samples.append(s)
        d_ota = s["ota"] - base["ota"]
        d_on = s["online"] - base["online"]
        d_off = s["offline"] - base["offline"]
        flag = "✅ 稳定" if d_ota == 0 else "⚠️ 设备重启过"
        print("  [%s] OTA +%d（累计 %d）  上线 +%d  断开 +%d  %s"
              % (s["time"], d_ota, s["ota"], d_on, d_off, flag))

    end = samples[-1]
    d_ota = end["ota"] - base["ota"]
    d_off = end["offline"] - base["offline"]
    mins = round((rounds * args.interval) / 60, 1)

    print("\n=== 结论 ===")
    if d_ota == 0:
        print("  ✅ 设备全程零重启（观察 %.1f 分钟）。" % mins)
        print("     屏幕不该出现『连接后台失败』。长连接正常挂着，可以直接唤醒对话。")
    else:
        print("  ⚠️ 观察 %.1f 分钟内设备重启 %d 次 —— 这是重启，不是断线。" % (mins, d_ota))
        print("     下一步：别碰串口，先查 device_dump/serial_final.log 里的 rst: 行。")

    if args.json_out:
        out = os.path.join(ROOT, args.json_out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"base": base, "samples": samples,
                       "conclusion": "stable" if d_ota == 0 else "rebooting",
                       "ota_delta": d_ota, "offline_delta": d_off}, f,
                      ensure_ascii=False, indent=2)
        print("  结果已写入 %s" % args.json_out)


if __name__ == "__main__":
    main()
