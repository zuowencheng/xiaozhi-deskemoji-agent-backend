# -*- coding: utf-8 -*-
"""gesture_voice_synth.py —— 合成手势开场白/退场语，直接落 Opus-in-Ogg

用途
----
为「手势唤醒开场白 / 手势关闭退场语」预生成本地 ogg 文件，
固件 PlaySound 本地播放：零延迟、不烧 TTS 额度、离线可用。

    python tools/gesture_voice_synth.py            # 合成两句
    python tools/gesture_voice_synth.py --dry      # 只打印不落盘

产物
----
firmware/community/main/assets/common/gesture_welcome.ogg  「我来啦，您请讲」
firmware/community/main/assets/common/gesture_bye.ogg      「那我退下了」

为什么用 format=ogg_opus（而不是 format=opus 自己封装）：
  format=opus 返回的是裸 opus 帧流，**没有包边界**（data 分块是网络分片
  不是包边界），固件 PlaySound 的逐包解码无从拆起；
  format=ogg_opus 火山直接返回完整 OggS 容器（含 OpusHead/lacing），
  与 assets/common/listening.ogg 同构，固件拿来就能播。
"""
import base64
import json
import struct
import sys
import uuid
import urllib.request

URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
RID = "volc.service_type.10029"                      # 1.0 通道（moon 音色）
SPEAKER = "zh_female_shuangkuaisisi_moon_bigtts"     # 双快思思 moon
APPID = "c105d0ce"                                   # 占位，_load_appid 会覆盖


def _load_appid():
    """从服务端配置读 appid，避免硬扛过期值。"""
    global APPID
    try:
        import re
        cfg = open("server/xiaozhi-server/data/.config.yaml", encoding="utf-8").read()
        m = re.search(r"HuoshanV3TTS:(.*?)(?=^\S|\Z)", cfg, re.S | re.M)
        seg = m.group(1) if m else cfg
        m2 = re.search(r"^\s+appid:\s*(\S+)", seg, re.M)
        if m2:
            APPID = m2.group(1)
    except Exception as e:
        print(f"[warn] 读配置 appid 失败，用默认: {e}")


def synth_ogg(text: str) -> bytes:
    """调火山 v3 合成，返回完整 ogg 文件字节。"""
    body = {"req_params": {
        "text": text,
        "speaker": SPEAKER,
        "additions": json.dumps({
            "disable_markdown_filter": True,
            "enable_language_detector": True,
            "disable_default_bit_rate": True,
        }, ensure_ascii=False),
        "audio_params": {
            "format": "ogg_opus",     # ★ 直出完整 ogg 容器
            "sample_rate": 16000,
            "speech_rate": 0,
            "loudness_rate": 0,
        },
    }}
    hdr = {
        "x-api-key": APPID,
        "X-Api-Resource-Id": RID,
        "X-Api-Request-Id": uuid.uuid4().hex,
        "Connection": "keep-alive",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(URL, json.dumps(body, ensure_ascii=False).encode("utf-8"), hdr)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=60) as r:
        raw = r.read().decode("utf-8", "replace")
    parts, errs = [], []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("data"):
            parts.append(base64.b64decode(j["data"]))
        code = j.get("code")
        if code not in (0, "0", None, 20000000, "20000000"):
            errs.append(f"{code} {str(j.get('message'))[:100]}")
    data = b"".join(parts)
    if not data or data[:4] != b"OggS":
        raise RuntimeError("没拿到 ogg: " + ("; ".join(errs[:2]) or f"头4字节={data[:4]!r}"))
    return data


def describe_ogg(data: bytes) -> str:
    """粗解析：声道/pre_skip/采样率/页数/时长。"""
    pos, pages, last_g, pre, ch, sr = 0, 0, 0, 0, 1, 16000
    head_seen = False
    while pos + 27 <= len(data) and data[pos:pos + 4] == b"OggS":
        n = data[pos + 26]
        segs = data[pos + 27:pos + 27 + n]
        body_off = pos + 27 + n
        # 重组 packet（跨 segment）
        cur, si, pkts = body_off, 0, []
        while si < n:
            ln, start = 0, cur
            while si < n:
                l = segs[si]; si += 1; ln += l; cur += l
                if l != 255:
                    break
            if ln:
                pkts.append(data[start:cur])
        for p in pkts:
            if not head_seen and p[:8] == b"OpusHead" and len(p) >= 19:
                _, ch, pre, sr = struct.unpack("<BBHI", p[8:16])
                head_seen = True
        g = struct.unpack("<Q", data[pos + 6:pos + 14])[0]
        if g != 0xFFFFFFFFFFFFFFFF:
            last_g = g
        pages += 1
        pos = body_off + sum(segs)
    dur = max(0.0, (last_g - pre) / 48000.0)
    return f"ch={ch} pre_skip={pre} sr={sr} pages={pages} 时长≈{dur:.2f}s"


def main():
    dry = "--dry" in sys.argv
    _load_appid()
    print(f"appid={APPID}  speaker={SPEAKER}  rid={RID}")
    jobs = [
        ("gesture_welcome", "我来啦，您请讲"),
        ("gesture_bye", "那我退下了"),
    ]
    for name, text in jobs:
        data = synth_ogg(text)
        print(f"[{name}] {text!r} -> {len(data)} 字节, {describe_ogg(data)}")
        if dry:
            print("  (dry-run，不落盘)")
            continue
        path = f"firmware/community/main/assets/common/{name}.ogg"
        with open(path, "wb") as f:
            f.write(data)
        print(f"  已写入 {path}")


if __name__ == "__main__":
    main()
