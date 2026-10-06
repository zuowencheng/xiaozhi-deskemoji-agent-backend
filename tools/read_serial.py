#!/usr/bin/env python3
"""读取 ESP32 串口日志（USB-Serial/JTAG 或 CH340/CP210x 均可）。

用法:
    python tools/read_serial.py <端口> [秒数] [波特率] [输出文件] [--reset]

例:
    python tools/read_serial.py COM3 30 115200 firmware/device_dump/serial_boot.log --reset

--reset: 打开后先做一次复位脉冲，用于抓完整启动日志。
         对 ESP32-S3 USB-Serial/JTAG: RTS=1 拉低 EN 触发复位。
"""
import sys
import time
import os

import serial


def reset_pulse(s):
    """ESP32 / ESP32-S3 复位脉冲。"""
    try:
        s.setDTR(False)
        s.setRTS(True)
        time.sleep(0.15)
        s.setRTS(False)
        time.sleep(0.05)
        s.setDTR(True)
        s.setRTS(False)
    except Exception as e:
        print("[reset] 复位脉冲失败: %s" % e)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_reset = "--reset" in sys.argv
    port = args[0] if len(args) > 0 else "COM3"
    secs = float(args[1]) if len(args) > 1 else 30.0
    baud = int(args[2]) if len(args) > 2 else 115200
    out = args[3] if len(args) > 3 else None

    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)

    s = serial.Serial()
    s.port = port
    s.baudrate = baud
    s.timeout = 0.3
    s.rtscts = False
    s.dsrdtr = False
    s.open()
    # ⚠️⚠️ 2026-10-03 铁证（别再改回去了）：
    #   串口日志里抓到 rst:0x15 (USB_UART_CHIP_RESET) —— **打开/关闭串口本身就会复位
    #   ESP32-S3**，跟固件崩不崩无关。排查"设备频繁重连"时务必先想起来这条。
    #   实测两版：
    #     ① open() 后 setDTR(False)/setRTS(False) → **打开瞬间就复位**（拉低 DTR 即复位脉冲的一半）
    #     ② 什么都不做（当前）                    → 抓完日志、close() 时才复位，不影响采集
    #   所以这里**绝对不要碰控制线**。

    if do_reset:
        print("[read_serial] 触发复位脉冲...")
        reset_pulse(s)
        time.sleep(0.2)

    print("[read_serial] 已打开 %s @ %d，采集 %.1f 秒..." % (port, baud, secs))

    buf = bytearray()
    t0 = time.time()
    try:
        while time.time() - t0 < secs:
            chunk = s.read(8192)
            if chunk:
                buf.extend(chunk)
                sys.stdout.write(chunk.decode("utf-8", "replace"))
                sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        s.close()

    print("\n[read_serial] 采集结束，共 %d 字节" % len(buf))
    if out:
        with open(out, "wb") as f:
            f.write(buf)
        print("[read_serial] 已写入 %s" % out)


if __name__ == "__main__":
    main()
