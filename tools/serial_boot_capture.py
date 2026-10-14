#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serial_boot_capture.py —— 主动复位设备并抓开机日志

为什么需要它：ESP32 的开机自检日志（PAJ7620 初始化、任务创建）只在复位瞬间打，
一旦错过就抓不到。serial_tap.py 是"不打扰"模式（不复位），所以抓不到开机段。

本脚本：开串口 → RTS 拉低复位（esptool HardReset 同款）→ 实时落盘 N 秒。
⚠️ 会复位设备（开机设置如手势模式会回默认）。

用法:
    python serial_boot_capture.py <端口> [秒数] [输出文件]
"""
import sys
import time

import serial


def main():
    args = sys.argv[1:]
    port = args[0] if len(args) > 0 else "COM4"
    secs = float(args[1]) if len(args) > 1 else 25.0
    out = args[2] if len(args) > 2 else "tmp/serial_boot.log"

    s = serial.Serial()
    s.port = port
    s.baudrate = 115200
    s.timeout = 0.3
    s.rtscts = False
    s.dsrdtr = False
    s.open()

    # 复位：EN 拉低再释放（等价 esptool HardReset；DTR 保持低 = IO0 高 = 正常启动）
    s.dtr = False
    s.rts = True
    time.sleep(0.2)
    s.rts = False
    print("[boot] 已复位 %s，采集 %.0f 秒 -> %s" % (port, secs, out), flush=True)

    t0 = time.time()
    total = 0
    with open(out, "wb") as f:
        while time.time() - t0 < secs:
            data = s.read(4096)
            if data:
                f.write(data)
                f.flush()
                total += len(data)
    s.close()
    print("[boot] 完成，%d 字节" % total, flush=True)


if __name__ == "__main__":
    main()
