#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
make_rollback_kit.py —— 制作「一键回滚包」

为什么单独做这个：
  16MB 全片备份能救命，但恢复要刷 16MB（慢，且会把 nvs 里的配网信息一起还原）。
  实际升级时最常用的是"只把 app 分区换回来"，几秒钟的事。
  所以这里把 ota_0（当前在跑的固件）、otadata（记录从哪个分区启动）、nvs（配网/激活信息）
  单独抠出来，配两个回滚脚本，分场景用。

用法：
  python make_rollback_kit.py --port COM3

产物（都在 hardware/rollback_kit/）：
  ota_0_app.bin        当前运行固件（4MB）
  otadata.bin          启动分区标记（8KB）
  nvs.bin              配网 / 激活信息（20KB）
  rollback_app.cmd     只回滚 app（推荐，10 秒内）
  rollback_full.cmd    全片回滚（救砖用，2~3 分钟）
  monitor.cmd          串口看启动日志（判断新固件有没有起来）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
KIT = BASE / "reports" / "rollback_kit"
FULL_BACKUP = BASE / "reports" / "backup_20261002_16MB.bin"
PY = sys.executable

# 分区表（实测自本机，见 docs/02-硬件档案.md）
PARTS = {
    "nvs":     (0x9000,   0x4000),
    "otadata": (0xD000,   0x2000),
    "ota_0":   (0x100000, 0x400000),
}


def esptool(port: str, *args: str) -> tuple[int, str]:
    cmd = [PY, "-m", "esptool", "--port", port, *args]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM3")
    args = ap.parse_args()

    KIT.mkdir(parents=True, exist_ok=True)
    print(f"回滚包目录：{KIT}")

    for name, (off, size) in PARTS.items():
        fname = "ota_0_app.bin" if name == "ota_0" else f"{name}.bin"
        out = KIT / fname
        print(f"读取 {name} @ 0x{off:X} ({size // 1024}KB) ...")
        code, log = esptool(args.port, "read-flash", hex(off), hex(size), str(out))
        if code != 0 or not out.exists():
            print(f"  [FAIL] {name}: {log[-500:]}")
            return 1
        print(f"  [OK] {out.name} {out.stat().st_size} bytes")

    # 只回滚 app：把当前固件和启动标记写回去
    (KIT / "rollback_app.cmd").write_text(
        "@echo off\n"
        "chcp 65001 >nul\n"
        "echo ==========================================\n"
        "echo  小智固件回滚（只回滚 app 分区，约 10 秒）\n"
        "echo ==========================================\n"
        f'"{PY}" -m esptool --port {args.port} write-flash 0xD000 "%~dp0otadata.bin"\n'
        f'"{PY}" -m esptool --port {args.port} write-flash 0x100000 "%~dp0ota_0_app.bin"\n'
        "echo.\n"
        "echo 回滚完成，按任意键重启设备查看效果\n"
        "pause\n", encoding="utf-8")

    # 全片回滚：救砖用
    has_full = FULL_BACKUP.exists()
    (KIT / "rollback_full.cmd").write_text(
        "@echo off\n"
        "chcp 65001 >nul\n"
        "echo ==========================================\n"
        "echo  小智全片回滚（16MB，约 2~3 分钟，救砖用）\n"
        "echo ==========================================\n"
        + (f'"{PY}" -m esptool --port {args.port} write-flash 0x0 "{FULL_BACKUP}"\n'
           if has_full else
           'echo [错误] 找不到全片备份 backup_20261002_16MB.bin，请先跑 scan_hardware.py\n')
        + "echo.\n"
        "echo 全片恢复完成\n"
        "pause\n", encoding="utf-8")

    (KIT / "monitor.cmd").write_text(
        "@echo off\n"
        "chcp 65001 >nul\n"
        "echo 串口监视中（115200），Ctrl+] 退出。用来判断新固件有没有正常启动。\n"
        f'"{PY}" -m serial.tools.miniterm {args.port} 115200\n', encoding="utf-8")

    print("\n=== 产物 ===")
    for f in sorted(KIT.iterdir()):
        print(f"  {f.name}  {f.stat().st_size} bytes")
    print("\n用法：")
    print("  升级后开不了机 → 双击 rollback_app.cmd（10 秒）")
    print("  彻底变砖 → 双击 rollback_full.cmd（2~3 分钟）")
    print("  想看启动日志 → 双击 monitor.cmd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
