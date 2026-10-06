#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asr_bench.py —— 本地 Sherpa vs 火山豆包 ASR 2.0 识别质量实测

方法：用**火山 TTS 合成**标准普通话音频（24kHz mp3）→ 重采样到 16kHz →
分别喂给本地 Sherpa（SenseVoice）和豆包 ASR 2.0 → 与原文逐字对比。

⚠️ 局限性（必须说明）：
  TTS 合成音比真人干净、无噪声、无口音、语速均匀，**所以这套测试对本地 ASR 有利**。
  真人在嘈杂环境、边想边说、语速不均时，本地模型会掉更多分。
  但它能客观测出**专有名词**的识别能力（这是本地模型最弱的一环）。

用法：python tools/asr_bench.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[1]
SRV = ROOT / "server" / "xiaozhi-server"
sys.path.insert(0, str(SRV))
sys.path.insert(0, str(ROOT / "tools"))
OUT = ROOT / "tmp" / "asr_bench"
OUT.mkdir(parents=True, exist_ok=True)

APPID = "c105d0ce-7912-49ea-a28e-81c97e5cfa12"
TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"

# 测试句：日常 / 专有名词 / 数字日期 三类
CASES = [
    ("日常", "主人，帮我看一下今天的天气怎么样"),
    ("专有名词", "小智，帮我打开 deskemoji 的表情设置"),
    ("专有名词", "把舵机的角度调一下，再试一次 WorkBuddy 的任务"),
    ("数字日期", "我明天早上八点要交房租，房租是两千三百五十元"),
    ("口语", "嗯，那个，你把客厅的灯关一下，然后再帮我倒杯水"),
]


def tts(text: str, path: Path) -> bool:
    body = {"req_params": {
        "text": text, "speaker": "zh_female_vv_uranus_bigtts",
        "additions": json.dumps({"disable_markdown_filter": True}),
        "audio_params": {"format": "mp3", "sample_rate": 24000}}}
    hdr = {"x-api-key": APPID, "X-Api-Resource-Id": "seed-tts-2.0",
           "X-Api-Request-Id": f"bench-{abs(hash(text))%10**8}",
           "Content-Type": "application/json"}
    r = requests.post(TTS_URL, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers=hdr, timeout=60)
    b64, err = [], None
    for line in r.content.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("data"):
            b64.append(j["data"])
        c = j.get("code")
        if c not in (0, "0", None, 20000000, "20000000"):
            err = f"{c} {j.get('message')}"
    if not b64:
        print(f"    TTS 失败: {err or r.text[:120]}")
        return False
    import base64
    path.write_bytes(base64.b64decode("".join(b64)))
    return True


def wav_to_16k_wav(wav: Path, ms_per_frame: int = 200):
    """读 16k wav，切成 200ms 一帧（火山官方建议双向流式单包 200ms 性能最优）。"""
    import wave as _w
    with _w.open(str(wav), "rb") as w:
        pcm = w.readframes(w.getnframes())
    d = np.frombuffer(pcm, dtype=np.int16)
    step = int(16000 * ms_per_frame / 1000)
    return [d[i:i + step].tobytes() for i in range(0, len(d), step)]


def to_16k_wav(mp3: Path, wav: Path) -> int:
    import soundfile as sf
    d, sr = sf.read(str(mp3), dtype="float32")
    if d.ndim > 1:
        d = d.mean(axis=1)
    # 线性重采样 24k -> 16k（够用；不做高阶滤波，避免引入额外失真）
    n = int(round(len(d) * 16000 / sr))
    x_old = np.arange(len(d))
    x_new = np.linspace(0, len(d) - 1, n)
    d16 = np.interp(x_new, x_old, d).astype(np.float32)
    pcm = (d16 * 32767).astype(np.int16)
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm.tobytes())
    return len(d16) / 16000


_LOCAL = {}


def local_sherpa(wav: Path) -> str:
    """直接用 sherpa_onnx（绕开 provider：它不支持 with，且相对 model_dir 会触发重复下载）。"""
    import sherpa_onnx
    md = SRV / "models" / "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
    if "m" not in _LOCAL:
        _LOCAL["m"] = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(md / "model.int8.onnx"),
            tokens=str(md / "tokens.txt"),
            use_itn=True, language="auto", num_threads=2)
    m = _LOCAL["m"]
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        pcm = w.readframes(w.getnframes())
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    s = m.create_stream()
    s.accept_waveform(sr, samples)
    m.decode_stream(s)
    return (s.result.text or "").strip()


# ⚠️ 必须在 asyncio.run 之前导入：config/settings.py 在 import 时就 asyncio.run(load_config())
import yaml
from core.providers.asr.doubao_stream import ASRProvider as _DoubaoProvider

_CFG = yaml.safe_load(open(SRV / "data" / ".config.yaml", encoding="utf-8"))["ASR"]["DoubaoASRStream"]


class _MockConn:
    """最小可用 conn（provider 会读 asr / asr_audio / client_* / stop_event）"""
    def __init__(self):
        self.conn_id = "bench"
        self.session_id = "bench"
        self.asr_audio = []
        self.stop_event = asyncio.Event()
        self.client_listen_mode = "auto"
        self.client_voice_stop = False
        self.client_voice_start = False
        self.client_have_voice = False

    def reset_audio_states(self):
        pass


async def _doubao_run(wav: Path) -> str:
    prov = _DoubaoProvider(_CFG, True)
    conn = _MockConn()
    conn.asr = prov
    frames = wav_to_16k_wav(wav)
    for fr in frames:
        conn.client_voice_start = True
        await prov.receive_audio(conn, fr, True)
        await asyncio.sleep(0.2)
    conn.client_voice_stop = True
    for _ in range(30):
        await asyncio.sleep(0.2)
        if (getattr(prov, "text", "") or "").strip():
            break
    conn.stop_event.set()
    await asyncio.sleep(0.3)
    # show_utterances=True 时 provider 只在 definite=True 才写 self.text，
    # 这里兜底从最后一条 utterances 拼
    got = (getattr(prov, "text", "") or "").strip()
    if not got:
        got = (getattr(prov, "last_stream_text", "") or "").strip()
    return got


def doubao(wav: Path) -> str:
    try:
        return asyncio.run(_doubao_run(wav))
    except Exception as e:
        return f"<异常 {type(e).__name__}: {str(e)[:40]}>"


def cer(ref: str, hyp: str) -> float:
    """字错误率（越低越好）。"""
    r, h = list(ref), list(hyp)
    if not r:
        return 0.0 if not h else 1.0
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            d[i][j] = min(d[i-1][j] + 1, d[i][j-1] + 1,
                          d[i-1][j-1] + (0 if r[i-1] == h[j-1] else 1))
    return d[len(r)][len(h)] / len(r)


def main():
    print("=" * 78)
    print("ASR 实测：本地 Sherpa（SenseVoice） vs  火山豆包流式 2.0")
    print("=" * 78)
    local_ok, doubao_ok = [], []
    for tag, text in CASES:
        mp3 = OUT / f"{tag}_{abs(hash(text)) % 9999}.mp3"
        wav = mp3.with_suffix(".wav")
        print(f"\n【{tag}】{text}")
        if not tts(text, mp3):
            continue
        dur = to_16k_wav(mp3, wav)
        lo = local_sherpa(wav)
        db = doubao(wav)
        e1, e2 = cer(text, lo), cer(text, db)
        local_ok.append(e1)
        if not db.startswith("<"):
            doubao_ok.append(e2)
        print(f"  时长 {dur:.1f}s")
        print(f"  本地  : {lo}   字错率 {e1*100:.0f}%")
        print(f"  豆包2.0: {db}   字错率 {e2*100:.0f}%" if not db.startswith("<")
              else f"  豆包2.0: {db}")
    print("\n" + "=" * 78)
    if local_ok:
        print(f"本地 Sherpa 平均字错率：{sum(local_ok)/len(local_ok)*100:.1f}%")
    if doubao_ok:
        print(f"豆包 2.0  平均字错率：{sum(doubao_ok)/len(doubao_ok)*100:.1f}%")
    print("=" * 78)
    print("⚠️ 局限性：TTS 音比真人干净，这套测试对本地 ASR 有利。")


if __name__ == "__main__":
    main()
