#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
deskemoji 硬件体检脚本（只读，安全）
用法：
  python scan_hardware.py                # 自动找 COM 口
  python scan_hardware.py --port COM5    # 指定串口
  python scan_hardware.py --skip-backup  # 只体检，不做全片备份（不建议）
产出：
  hardware/reports/hardware_report_<日期>.md   体检报告
  hardware/reports/backup_<日期>_16MB.bin      全片备份（原厂固件保命用，勿上传 GitHub）
依赖：venv 里 pip install esptool pyserial
"""
import argparse
import datetime
import re
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "reports"
REPORTS.mkdir(exist_ok=True)
TODAY = datetime.date.today().strftime("%Y%m%d")


def run_esptool(port: str, args: list[str], timeout=120):
    """调用 venv 里的 esptool，返回 (ok, stdout)"""
    exe = sys.executable
    cmd = [exe, "-m", "esptool", "--port", port] + args
    print(f"\n>>> {' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out
    except subprocess.TimeoutExpired:
        return False, "[TIMEOUT]"


def list_ports() -> list[str]:
    try:
        import serial.tools.list_ports
        return [p.device for p in serial.tools.list_ports.comports()]
    except ImportError:
        print("缺少 pyserial：pip install pyserial")
        sys.exit(1)


def parse_field(text: str, pattern: str):
    m = re.search(pattern, text)
    return m.group(1).strip() if m else "未识别"


def parse_partitions(bin_data: bytes):
    """解析 ESP32 分区表（每条 32 字节，magic 0xAA 0x50）"""
    names = {0: "app", 1: "data"}
    out = []
    for i in range(0, len(bin_data) - 31, 32):
        e = bin_data[i:i + 32]
        if e[0:2] != b"\xAA\x50":
            break
        ptype, sub, off, size = e[2], e[3], *struct.unpack("<II", e[4:12])
        name = e[12:28].split(b"\x00")[0].decode("ascii", "replace")
        out.append((names.get(ptype, str(ptype)), sub, name, off, size))
    return out


def human(n: int):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def parse_app_desc(bin_path: Path):
    """从全片备份里解析 esp_app_desc_t，拿到固件版本/项目名/编译时间/IDF 版本"""
    d = bin_path.read_bytes()
    for slot in (0x100000, 0x500000):  # ota_0 / ota_1
        i = d.find(b"\x32\x54\xcd\xab", slot, slot + 0x400)  # ESP_APP_DESC_MAGIC_WORD
        if i < 0:
            continue
        g = lambda a, b: d[i + a:i + b].split(b"\x00")[0].decode("ascii", "replace")
        # 实测偏移（ESP-IDF v5.5 的 esp_app_desc_t）：version@16 project@48 time@80 date@96 idf@128
        return {"slot": hex(slot), "version": g(16, 48), "project": g(48, 64),
                "time": g(80, 96), "date": g(96, 112), "idf": g(112, 144)}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="串口，如 COM5")
    ap.add_argument("--skip-backup", action="store_true")
    args = ap.parse_args()

    port = args.port
    if not port:
        ports = list_ports()
        print("检测到串口：", ports)
        if len(ports) == 1:
            port = ports[0]
        else:
            print("找到多个/零个串口，请用 --port 指定（设备管理器里看 USB 串行设备）")
            sys.exit(1)

    lines = [f"# deskemoji 硬件体检报告 · {datetime.datetime.now():%Y-%m-%d %H:%M}", f"串口：`{port}`\n"]

    # 1. 芯片信息 + Flash
    ok, out = run_esptool(port, ["flash-id"])
    if not ok:
        print("\n【失败】连不上芯片。排查顺序：\n"
              "  1) 换一个 Type-C 口再试（板上有两个，另一个可能是纯供电口）\n"
              "  2) 按住 BOOT 键不放 → 拔插 USB → 松开，再跑一次\n"
              "  3) 装 CP210x/CH34x 串口驱动（设备管理器有无黄色感叹号）\n")
        lines.append("## ❌ 无法连接芯片\n```\n" + out[-1500:] + "\n```")
        (REPORTS / f"hardware_report_{TODAY}.md").write_text("\n".join(lines), encoding="utf-8")
        sys.exit(2)

    # 兼容 esptool v5 的输出格式（Chip type / Crystal frequency / MAC:）
    chip = parse_field(out, r"Chip type:\s*([^\s(]+)")
    revision = parse_field(out, r"\(revision\s+([^)]+)\)")
    crystal = parse_field(out, r"Crystal frequency:\s*(\S+)")
    psram = parse_field(out, r"PSRAM[^,\n]*?(\d+MB)")
    flash_man = parse_field(out, r"Manufacturer:\s*(\w+)")
    flash_dev = parse_field(out, r"Device:\s*(\w+)")
    flash_size = parse_field(out, r"Detected flash size:\s*(\d+[KM]B)")
    mac = parse_field(out, r"MAC:\s*([0-9a-fA-F:]+)")

    lines += [
        "## 芯片 / 存储",
        f"- 芯片：**{chip}**（revision {revision}，晶振 {crystal}）",
        f"- Flash：{flash_man} {flash_dev}，大小 **{flash_size}**",
        f"- PSRAM：**{psram}**（实测自 esptool Features 行）",
        f"- MAC：`{mac}`",
        "",
    ]

    # 2. 分区表
    pt_file = REPORTS / f"partition_table_{TODAY}.bin"
    ok, _ = run_esptool(port, ["read-flash", "0x8000", "0xC00", str(pt_file)])
    lines.append("## 分区表")
    if ok and pt_file.exists():
        rows = parse_partitions(pt_file.read_bytes())
        lines.append("| 类型 | 子类型 | 名称 | 偏移 | 大小 |")
        lines.append("|---|---|---|---|---|")
        for ptype, sub, name, off, size in rows:
            lines.append(f"| {ptype} | {sub:#x} | {name} | {off:#x} | {human(size)} |")
    else:
        lines.append("读取失败（可稍后重试，不影响备份）")
    lines.append("")

    # 3. 全片备份
    if not args.skip_backup:
        size_mb = int(re.match(r"(\d+)", flash_size).group(1)) if "MB" in flash_size else 16
        total = size_mb * 1024 * 1024
        backup = REPORTS / f"backup_{TODAY}_{size_mb}MB.bin"
        if backup.exists() and backup.stat().st_size == total:
            print(f"备份已存在，跳过：{backup}")
        else:
            print(f"\n>>> 开始全片备份（{size_mb}MB，只读操作，预计几分钟）...")
            ok, out = run_esptool(port, ["read-flash", "0", str(total), str(backup)], timeout=1800)
            if ok and backup.exists():
                lines.append(f"## 备份 ✅\n- `{backup.name}`（{backup.stat().st_size} 字节）——原厂固件保命，**勿上传 GitHub**")
            else:
                lines.append("## 备份 ❌ 失败，需重试")

    # 4. 固件身份（从已有备份解析，不需要再连芯片）
    baks = sorted(REPORTS.glob("backup_*.bin"))
    if baks:
        fw = parse_app_desc(baks[-1])
        if fw:
            lines += [
                "## 固件身份（从备份解析 esp_app_desc）",
                f"- 版本：**{fw['version']}**　项目名：`{fw['project']}`",
                f"- 编译时间：{fw['date']} {fw['time']}",
                f"- ESP-IDF：{fw['idf']}　运行分区：{fw['slot']}",
                "",
            ]

    report = REPORTS / f"hardware_report_{TODAY}.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 报告已生成：{report}")


if __name__ == "__main__":
    main()
