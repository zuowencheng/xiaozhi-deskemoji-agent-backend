#!/usr/bin/env python3
"""设备头部解析 + otadata 补丁生成（规则已按 ESP-IDF v5.5.4 源码核实）。

关键规则（源码依据 bootloader_support/src/）：
  bootloader_common_ota_select_crc()  = esp_rom_crc32_le(UINT32_MAX, &s->ota_seq, 4)
                                      → 只对前 4 字节 ota_seq 算 CRC
  bootloader_common_ota_select_valid()= seq != 0xFFFFFFFF && state not in (INVALID,ABORTED)
                                        && crc == ota_select_crc()
  选分区: boot_index = (ota_seq - 1) % app_count
  slot 选择: 两个 slot 都有效时取 ota_seq 较大者

分区表结构（esp_flash_partitions.h）:
  uint16 magic(0x50AA) / uint8 type / uint8 subtype / {uint32 offset; uint32 size}
  / uint8 label[16] / uint32 flags   共 32 字节

用法:
  python tools/otahelper.py parse <bin>                 # 解析分区表 + otadata
  python tools/otahelper.py mkotadata <seq> <out.bin>   # 生成 32 字节 ota_select 记录
  python tools/otahelper.py patchslot <slot> <seq> <out.bin>  # 生成 0x2000 的 otadata 替换镜像
"""
import sys
import struct

ESP_PARTITION_MAGIC = 0x50AA
APP_SUBTYPES = {0x00: "factory", 0x10: "ota_0", 0x11: "ota_1", 0x12: "ota_2", 0x20: "test"}
DATA_SUBTYPES = {0x00: "ota", 0x01: "phy", 0x02: "nvs", 0x03: "coredump", 0x04: "nvs_keys",
                 0x06: "spiffs", 0x81: "fat", 0x82: "littlefs"}
OTA_STATES = {0: "NEW", 1: "PENDING_VERIFY", 2: "VALID", 3: "INVALID", 4: "ABORTED",
              0xFFFFFFFF: "UNDEFINED(0xFF)"}

# ---- CRC table (reflected, poly 0xEDB88320) ----
_TAB = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0xEDB88320 if _c & 1 else _c >> 1
    _TAB.append(_c)


def ota_select_crc(ota_seq_le4):
    """等价 esp_rom_crc32_le(UINT32_MAX, &ota_seq, 4)：init 0、末尾异或 0xFFFFFFFF。"""
    c = 0
    for b in ota_seq_le4:
        c = _TAB[(c ^ b) & 0xFF] ^ (c >> 8)
    return c ^ 0xFFFFFFFF


def parse_partitions(buf):
    print("=== 分区表 ===")
    print("  %-12s %-6s %-12s %-11s %-11s %s" % ("Name", "Type", "SubType", "Offset", "Size", "End"))
    parts = []
    for i in range(0, min(len(buf), 0x1000), 32):
        e = buf[i:i + 32]
        magic, = struct.unpack("<H", e[0:2])
        if magic != ESP_PARTITION_MAGIC:
            if i == 0:
                print("  !! magic 不是 0x50AA，可能不是分区表")
            break
        ptype, subtype = e[2], e[3]
        off, size = struct.unpack("<II", e[4:12])
        label = e[12:28].split(b"\x00")[0].decode("utf-8", "replace")
        flags, = struct.unpack("<I", e[28:32])
        tn = {0: "app", 1: "data"}.get(ptype, "?")
        st = (APP_SUBTYPES if ptype == 0 else DATA_SUBTYPES).get(subtype, "0x%02X" % subtype)
        print("  %-12s %-6s %-12s 0x%-9X 0x%-9X 0x%-9X" % (label, tn, st, off, size, off + size))
        parts.append(dict(label=label, type=tn, subtype=st, offset=off, size=size))
    app_count = sum(1 for p in parts if p["type"] == "app" and p["subtype"].startswith("ota_"))
    print("  -> app_count(ota_*) = %d   [选分区公式 (seq-1) %% app_count]" % app_count)
    return parts, app_count


def parse_otadata(buf, app_count=2, base_off=0x5000, flash_base=0xD000):
    """base_off: otadata 在输入文件内的偏移（输入若是 0x8000 起的头部快照则为 0x5000；
    若输入本身就是 0xD000 起的 otadata_live.bin 则为 0）。"""
    print()
    print("=== otadata (flash 0x%X) ===" % flash_base)
    for slot in range(2):
        o = base_off + slot * 0x1000
        e = buf[o:o + 32]
        if len(e) < 32:
            print("  slot%d: 数据不足" % slot)
            continue
        seq, = struct.unpack("<I", e[0:4])
        state, = struct.unpack("<I", e[24:28])
        crc, = struct.unpack("<I", e[28:32])
        calc = ota_select_crc(e[0:4])
        valid = (seq != 0xFFFFFFFF and state not in (3, 4) and crc == calc)
        print("  slot%d @flash 0x%X  seq=%-14s state=%-16s crc=0x%08X 校验:%s" % (
            slot, flash_base + slot * 0x1000,
            "0xFFFFFFFF(空)" if seq == 0xFFFFFFFF else seq,
            OTA_STATES.get(state, "?"), crc,
            "OK" if crc == calc else "MISMATCH(应为0x%08X)" % calc))
        if valid:
            print("        -> 有效; 映射 (%d-1)%%%d = %d => ota_%d" % (
                seq, app_count, (seq - 1) % app_count, (seq - 1) % app_count))
        else:
            print("        -> 无效，bootloader 会忽略")


def mk_record(seq, state=2):
    rec = struct.pack("<I", seq) + b"\xFF" * 20 + struct.pack("<I", state)
    crc = ota_select_crc(rec[0:4])
    return rec + struct.pack("<I", crc)


def main():
    mode = sys.argv[1]
    if mode == "parse":
        buf = open(sys.argv[2], "rb").read()
        print("文件: %s (%d 字节)\n" % (sys.argv[2], len(buf)))
        _, ac = parse_partitions(buf)
        parse_otadata(buf, ac)
    elif mode == "mkotadata":
        seq = int(sys.argv[2])
        r = mk_record(seq)
        open(sys.argv[3], "wb").write(r)
        print("seq=%d 记录 (%d 字节) -> %s" % (seq, len(r), sys.argv[3]))
        print("  hex:", r.hex())
        print("  映射: (%d-1)%%2 = %d => ota_%d" % (seq, (seq - 1) % 2, (seq - 1) % 2))
    elif mode == "patchslot":
        slot = int(sys.argv[2])
        seq = int(sys.argv[3])
        out = sys.argv[4]
        img = b"\xFF" * 0x2000
        rec = mk_record(seq)
        off = slot * 0x1000
        img = img[:off] + rec + img[off + 32:]
        open(out, "wb").write(img)
        print("0x2000 otadata 镜像，slot%d=seq%d -> %s" % (slot, seq, out))
        print("  注意: 另一 slot 也是 0xFF，会覆盖设备上的旧记录；用 write_flash -1 | 只写 0x40 字节更安全")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
