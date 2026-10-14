#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serial_tap.py —— 实时落盘的串口日志采集（read_serial.py 的实时版）

read_serial.py 是采集结束才写文件，中途一打断就全丢。
本脚本每收到一块就 write + flush，任何时候 kill 都有完整现场。

用法:
    python serial_tap.py <端口> [秒数] [波特率] [输出文件]

⚠️ 铁律（2026-10-03 实测）：打开/关闭串口本身就会复位 ESP32-S3（rst:0x15）。
   所以本脚本**绝对不碰 DTR/RTS 控制线**，采集期间设备行为不受影响。
"""
import sys
import time

import serial


def main():
    args = sys.argv[1:]
    port = args[0] if len(args) > 0 else "COM4"
    secs = float(args[1]) if len(args) > 1 else 60.0
    baud = int(args[2]) if len(args) > 2 else 115200
    out = args[3] if len(args) > 3 else "tmp/serial_tap.log"

    s = serial.Serial()
    s.port = port
    s.baudrate = baud
    s.timeout = 0.3
    s.rtscts = False
    s.dsrdtr = False
    s.open()
    # ⚠️ 不 setDTR/setRTS，理由见模块 docstring。

    print("[tap] 已打开 %s @ %d，实时写入 %s，采集 %.0f 秒" % (port, baud, out, secs), flush=True)
    total = 0
    t0 = time.time()
    with open(out, "wb") as f:
        try:
            while time.time() - t0 < secs:
                chunk = s.read(8192)
                if chunk:
                    f.write(chunk)
                    f.flush()
                    total += len(chunk)
        except KeyboardInterrupt:
            pass
        finally:
            s.close()
    print("[tap] 采集结束，共 %d 字节 -> %s" % (total, out), flush=True)


if __name__ == "__main__":
    main()
