#!/usr/bin/env python3
"""解析从设备读回的 0x8000 起始 32KB 头部：
   - 0x8000..0x9000 分区表
   - 0xd000..0xf000 otadata（两个 ota_select slot）
用法: python tools/parse_device_head.py <bin路径>
"""
import sys
import struct
import zlib

PART_TYPES = {0x00: "app", 0x01: "data"}
APP_SUBTYPES = {0x00: "factory", 0x10: "ota_0", 0x11: "ota_1", 0x12: "ota_2", 0x20: "test"}
DATA_SUBTYPES = {0x00: "ota", 0x01: "phy", 0x02: "nvs", 0x03: "coredump", 0x04: "nvs_keys",
                 0x06: "spiffs", 0x81: "fat", 0x82: "fat", 0x06: "spiffs"}
OTA_STATES = {0: "NEW", 1: "PENDING_VERIFY", 2: "VALID", 3: "INVALID", 4: "ABORTED",
              0xFFFFFFFF: "UNDEFINED(0xFF..)"}


def parse_partitions(buf, base=0x8000):
    print("=== 分区表 (flash 0x%X) ===" % base)
    print("  %-16s %-8s %-12s %-10s %-10s" % ("Name", "Type", "SubType", "Offset", "Size"))
    parts = []
    for i in range(0, 0x1000, 32):
        e = buf[i:i + 32]
        if len(e) < 32:
            break
        magic, ptype, subtype = e[0], e[1], e[2]
        if magic != 0xAA:
            break
        label = e[4:20].split(b"\x00")[0].decode("utf-8", "replace")
        pos, size, flags = struct.unpack("<III", e[20:32])
        tn = PART_TYPES.get(ptype, "?")
        st = (APP_SUBTYPES if ptype == 0 else DATA_SUBTYPES).get(subtype, "?")
        print("  %-16s %-8s %-12s 0x%-8X 0x%-8X" % (label, tn, st, pos, size))
        parts.append((label, tn, st, pos, size))
    return parts


def parse_otadata(buf, off=0x5000):
    """otadata 相对文件偏移: 0xd000-0x8000 = 0x5000"""
    print()
    print("=== otadata (flash 0xD000) ===")
    for slot in range(2):
        o = off + slot * 0x1000
        e = buf[o:o + 32]
        seq, = struct.unpack("<I", e[0:4])
        label = e[4:24].split(b"\x00")[0].decode("utf-8", "replace")
        state, = struct.unpack("<I", e[24:28])
        crc_stored, = struct.unpack("<I", e[28:32])
        crc_calc = zlib.crc32(e[0:28]) & 0xFFFFFFFF
        ok = (crc_stored == crc_calc)
        empty = all(b == 0xFF for b in e)
        print("  slot%d @0x%X: ota_seq=%s (0x%08X)  state=%s(0x%X)  crc=%s  %s" % (
            slot, 0xD000 + slot * 0x1000,
            "空(0xFFFFFFFF)" if seq == 0xFFFFFFFF else seq, seq,
            OTA_STATES.get(state, "?"), state,
            "OK" if ok else "MISMATCH(0x%08X vs 0x%08X)" % (crc_stored, crc_calc),
            "全FF(未初始化)" if empty else ""))
        if not empty and ok and seq != 0xFFFFFFFF:
            tgt = "ota_0" if seq % 2 == 0 else "ota_1"
            print("       -> 该 slot 有效；按 ota_seq 奇偶推导: seq%%2=%d => 指向 %s" % (seq % 2, tgt))


def main():
    p = sys.argv[1]
    buf = open(p, "rb").read()
    print("文件: %s  (%d 字节)" % (p, len(buf)))
    print()
    parse_partitions(buf, 0x8000)
    parse_otadata(buf)


if __name__ == "__main__":
    main()
