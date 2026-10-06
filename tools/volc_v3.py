#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""volc_v3.py —— 火山语音合成 v3 客户端（HTTP unidirectional）

★★ 2026-10-03 踩坑总结（之前一直 3001 / 401 / 55000000，就是这三点）★★

1) **鉴权只发 `x-api-key: <APPID>`，不发 access_token**
   （access_token 是 v1 老接口用的；v3 HTTP 根本不看它）
   端点  POST https://openspeech.bytedance.com/api/v3/tts/unidirectional

2) **resource_id 必须和音色匹配**，否则报
   `code=55000000 resource ID is mismatched with speaker related resource`
   ┌─────────────────────────────────┬──────────────────────────┐
   │ 音色后缀                        │ resource_id              │
   ├─────────────────────────────────┼──────────────────────────┤
   │ _moon_bigtts / _mars_bigtts     │ volc.service_type.10029  │ 语音合成大模型
   │ _uranus_bigtts（含 vv / xiaohe）│ seed-tts-2.0             │ 豆包语音合成模型2.0
   └─────────────────────────────────┴──────────────────────────┘
   ★ 主人指定的 zh_female_vv_uranus_bigtts 属于后者，必须配 seed-tts-2.0
   ★ 火山 v1 文档明写：「不支持豆包语音合成模型2.0 的音色，比如 zh_female_vv_uranus_bigtts，
     如需使用推荐使用 v3 接口」—— 这就是之前 v1 一直 3001 的原因。

3) **响应是分块 JSON 流**（一行一个 JSON 对象），不是单个 JSON。
   整块 json.loads 会报 "Extra data: line 2" —— 必须逐行解析再把 data 拼起来。

用法：
    python tools/volc_v3.py list                 # 扫两组 resource_id × 候选音色
    python tools/volc_v3.py one <speaker> [rid]  # 测单个音色
    python tools/volc_v3.py gen <speaker> <文本> [rate] [rid]
"""
from __future__ import annotations

import base64
import json
import os
import sys

import requests

APPID = os.environ.get("VOLC_APPID", "c105d0ce-7912-49ea-a28e-81c97e5cfa12")
URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"

RID_MEGA = "volc.service_type.10029"   # 语音合成大模型（_moon / _mars）
RID_SEED = "seed-tts-2.0"              # 豆包语音合成模型2.0（_uranus）

VOICES = [
    ("zh_female_vv_uranus_bigtts", "主人指定·vv", RID_SEED),
    ("zh_female_xiaohe_uranus_bigtts", "小何 2.0", RID_SEED),
    ("zh_female_wanwanxiaohe_uranus_bigtts", "湾湾小何 uranus", RID_SEED),
    ("zh_female_chunsheng_uranus_bigtts", "纯纯 uranus", RID_SEED),
    ("zh_female_yaoyao_uranus_bigtts", "瑶瑶 uranus", RID_SEED),
    ("zh_female_wanwanxiaohe_moon_bigtts", "湾湾小何 V1", RID_MEGA),
    ("zh_female_shuangkuaisisi_moon_bigtts", "双快四丝", RID_MEGA),
    ("zh_male_yuanboxiaoshu_moon_bigtts", "远变小叔叔", RID_MEGA),
    ("zh_male_beijingxiaoye_emo_v2_mars_bigtts", "北京晓爷(男)", RID_MEGA),
]

OUT = "tmp"


def synth(speaker: str, text: str, rid: str = None,
          rate: int = 0, pitch: int = 0, save_as: str = None) -> tuple:
    """返回 (ok, 说明)。ok=True 时 mp3 已落盘。"""
    rid = rid or RID_SEED
    additions = {"disable_markdown_filter": True, "enable_language_detector": True,
                 "disable_default_bit_rate": True}
    if pitch:
        additions["post_process"] = {"pitch": pitch}
    body = {"req_params": {
        "text": text,
        "speaker": speaker,
        "additions": json.dumps(additions, ensure_ascii=False),
        "audio_params": {"format": "mp3", "sample_rate": 24000,
                         "speech_rate": rate, "loudness_rate": 0},
    }}
    hdr = {"x-api-key": APPID, "X-Api-Resource-Id": rid,
           "Connection": "keep-alive", "Content-Type": "application/json"}
    try:
        r = requests.post(URL, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers=hdr, timeout=60)
    except Exception as e:
        return False, "请求异常 " + str(e)[:70]
    if r.status_code != 200:
        return False, "HTTP %s %s" % (r.status_code, r.text[:120])

    # ★ 分块 JSON 流：逐行解析，把每段 data 拼起来
    body_text = r.content.decode("utf-8", errors="replace")
    b64, errs = [], []
    for line in body_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("data"):
            b64.append(j["data"])
        code = j.get("code")
        if code not in (0, "0", None):
            errs.append("%s %s" % (code, str(j.get("message"))[:90]))
    if not b64:
        return False, ("code=" + errs[0]) if errs else ("无 data 块: " + body_text[:120])
    try:
        raw = base64.b64decode("".join(b64).replace("\n", "").replace("\r", ""))
    except Exception as e:
        return False, "base64 解码失败 " + str(e)[:50]
    if len(raw) < 200:
        return False, "音频过小 %d 字节（%s）" % (len(raw), errs[:1])
    os.makedirs(OUT, exist_ok=True)
    fn = save_as or "%s/v3_%s.mp3" % (OUT, speaker)
    with open(fn, "wb") as f:
        f.write(raw)
    return True, "%d 字节 -> %s" % (len(raw), fn)


def scan():
    print("=" * 74)
    print("火山语音合成 v3 扫描   APPID=%s" % APPID)
    print("=" * 74)
    ok = []
    for sp, note, rid in VOICES:
        good, msg = synth(sp, "这是音色试听，小智在跟你说话。", rid=rid)
        print("  %s %-40s %-14s %s" % ("OK " if good else "-- ", sp, note, msg))
        if good:
            ok.append((sp, note, rid, "%s/v3_%s.mp3" % (OUT, sp)))
    print("-" * 74)
    if ok:
        print("可用的音色 %d 个：" % len(ok))
        for sp, note, rid, f in ok:
            print("   %s" % sp)
            print("      %s · rid=%s · %s" % (note, rid, f))
    else:
        print("一个都没通。确认已开通「豆包语音合成模型2.0」和/或「语音合成大模型」")
    return ok


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "one":
        sp = sys.argv[2]
        rid = sys.argv[3] if len(sys.argv) > 3 else RID_SEED
        good, msg = synth(sp, "主人，这是音色试听。", rid=rid)
        print(("OK  " if good else "FAIL"), sp, rid, "->", msg)
    elif cmd == "gen":
        sp = sys.argv[2]
        txt = sys.argv[3] if len(sys.argv) > 3 else "主人，这是音色试听。"
        rate = int(sys.argv[4]) if len(sys.argv) > 4 else 0
        rid = sys.argv[5] if len(sys.argv) > 5 else RID_SEED
        good, msg = synth(sp, txt, rid=rid, rate=rate,
                          save_as="%s/preview_%s_r%d.mp3" % (OUT, sp, rate))
        print(("OK  " if good else "FAIL"), sp, "->", msg)
    else:
        scan()
