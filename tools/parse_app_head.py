#!/usr/bin/env python3
"""解析 app 分区镜像头（esp_app_desc_t）+ otadata 文件。

用法:
    python tools/parse_app_head.py <bin> [<bin> ...]

自动识别: 若文件在 0x20 处有 esp_app_desc magic(0xABCD5432) 则按 app 解析；
          若第 32 字节是 crc 且 0x0000/0x1000 有两个 32 字节记录，则按 otadata 解析。
"""
import sys
import struct
import zlib

APP_MAGIC = 0xABCD5432
OTA_STATES = {0: "NEW", 1: "PENDING_VERIFY", 2: "VALID", 3: "INVALID", 4: "ABORTED",
              0xFFFFFFFF: "UNDEFINED(0xFF)"}


def try_app_desc(buf):
    for off in (0x20, 0x0):
        if len(buf) < off + 0x100:
            continue
        m, = struct.unpack("<I", buf[off:off + 4])
        if m != APP_MAGIC:
            continue
        sec, = struct.unpack("<I", buf[off + 4:off + 8])
        ver = buf[off + 16:off + 48].split(b"\x00")[0].decode("utf-8", "replace")
        proj = buf[off + 48:off + 80].split(b"\x00")[0].decode("utf-8", "replace")
        tm = buf[off + 80:off + 96].split(b"\x00")[0].decode("utf-8", "replace")
        dt = buf[off + 96:off + 112].split(b"\x00")[0].decode("utf-8", "replace")
        idf = buf[off + 112:off + 144].split(b"\x00")[0].decode("utf-8", "replace")
        return dict(off=off, secure_version=sec, version=ver, project=proj,
                    time=tm, date=dt, idf_ver=idf)
    return None


def show_app(buf):
    d = try_app_desc(buf)
    if not d:
        return False
    print("  [APP 镜像] esp_app_desc @0x%X" % d["off"])
    print("    version   : %s" % d["version"])
    print("    project   : %s" % d["project"])
    print("    build time: %s %s" % (d["date"], d["time"]))
    print("    idf_ver   : %s" % d["idf_ver"])
    print("    secure_ver: %d" % d["secure_version"])
    return True


def show_otadata(buf):
    print("  [otadata] 两个 32 字节 ota_select 记录")
    for slot in range(2):
        o = slot * 0x1000
        e = buf[o:o + 32]
        if len(e) < 32:
            break
        seq, = struct.unpack("<I", e[0:4])
        label = e[4:24]
        state, = struct.unpack("<I", e[24:28])
        crc_stored, = struct.unpack("<I", e[28:32])
        empty = all(b == 0xFF for b in e)
        print("    slot%d @+0x%X: seq=%s state=%s(0x%X) crc_stored=0x%08X %s" % (
            slot, o,
            "空" if seq == 0xFFFFFFFF else str(seq),
            OTA_STATES.get(state, "?"), state, crc_stored,
            "[全FF]" if empty else ""))
        if empty:
            continue
        # 试各种 CRC 变体
        cand = {
            "zlib.crc32(d[:28])": zlib.crc32(e[0:28]) & 0xFFFFFFFF,
            "zlib.crc32(d[:24])": zlib.crc32(e[0:24]) & 0xFFFFFFFF,
            "zlib.crc32(seq+label)": zlib.crc32(e[0:24]) & 0xFFFFFFFF,
        }
        for k, v in cand.items():
            if v == crc_stored:
                print("        -> CRC 命中: %s" % k)
                break
        else:
            print("        -> CRC 未命中任何变体；候选: " +
                  ", ".join("%s=0x%08X" % (k, v) for k, v in cand.items()))
        if seq != 0xFFFFFFFF:
            print("        -> seq%%2=%d => 指向 %s" % (seq % 2, "ota_0" if seq % 2 == 0 else "ota_1"))
    return True


def main():
    for p in sys.argv[1:]:
        buf = open(p, "rb").read()
        print("=" * 70)
        print("文件: %s (%d 字节)" % (p, len(buf)))
        hit = show_app(buf)
        if not hit:
            print("  (无 app desc，按 otadata 解析)")
        show_otadata(buf)
        print()


if __name__ == "__main__":
    main()
