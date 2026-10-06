#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asr_doubao_test.py —— 用真实 provider 测豆包流式 ASR 2.0（不走自造帧）

起因：昨天 asr_bench.py 用 JSON 帧测失败，那是**我脚本用错协议**——
小智 provider 用的 `generate_header + gzip` 二进制帧才是 v3 sauc 的正确格式。
所以这次直接用 provider，只 mock 一个最小 conn。

用法：python tools/asr_doubao_test.py [wav路径]
"""
from __future__ import annotations

import asyncio
import os
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRV = ROOT / "server" / "xiaozhi-server"
sys.path.insert(0, str(SRV))


class MockConn:
    """最小可用的 ConnectionHandler 替身"""

    def __init__(self, conn_id="test-conn"):
        self.conn_id = conn_id
        self.session_id = "bench-session"
        self.asr_audio = []
        self.stop_event = asyncio.Event()
        self.client_listen_mode = "auto"
        self.client_voice_stop = False
        self.client_voice_start = False
        self.conn_state = {}
        self.voice_text_buffer = ""
        self.recognized = []

    def reset_audio_states(self):
        pass

    def get(self, k, d=None):
        return getattr(self, k, d)


def wav_to_frames(path: Path, ms_per_frame: int = 200):
    """按 200ms 切帧（火山官方建议：双向流式单包 200ms 性能最优）。"""
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        pcm = w.readframes(w.getnframes())
    data = np.frombuffer(pcm, dtype=np.int16)
    step = int(sr * ms_per_frame / 1000)
    return [data[i:i + step].tobytes() for i in range(0, len(data), step)], sr


# ⚠️ 必须在 asyncio.run 之前导入：config/settings.py 在 import 时就 asyncio.run(load_config())，
#    在事件循环里再导入会抛 "asyncio.run() cannot be called from a running event loop"
import yaml
from core.providers.asr.doubao_stream import ASRProvider

CFG = yaml.safe_load(open(SRV / "data" / ".config.yaml", encoding="utf-8"))["ASR"]["DoubaoASRStream"]


async def run(wav: Path, text_hint: str = ""):
    cfg = CFG
    prov = ASRProvider(cfg, True)
    print("=" * 70)
    print(f"豆包流式 ASR 2.0 实测（用真实 provider）{text_hint}")
    print("=" * 70)
    print(f"  ws_url      = {prov.ws_url}")
    print(f"  resource_id = {prov.resource_id}")
    print(f"  鉴权头      = {prov._pick_auth()}")
    print(f"  热词表      = {cfg.get('boosting_table_name') or '(未配置)'}")

    frames, sr = wav_to_frames(wav)
    print(f"  音频        = {wav.name}  {sr}Hz  {len(frames)} 帧 × 200ms = {len(frames)*0.2:.1f}s")
    print("-" * 70)

    conn = MockConn()
    conn.asr = prov            # base.receive_audio 会读 conn.asr.interface_type
    text_out = ""

    # ⚠️ 不要自己再跑一遍 _forward_asr_results ——
    #    provider 在 receive_audio 内部已经 asyncio.create_task 起了接收任务，
    #    再跑一份会两个协程抢同一个 ws.recv()，结果互相偷帧（表现就是"没文本"）
    for fr in frames:
        conn.client_voice_start = True
        await prov.receive_audio(conn, fr, True)
        await asyncio.sleep(0.2)          # 按 200ms 节奏发，别太快
    # 结束：告诉 VAD 说完
    conn.client_voice_stop = True
    for _ in range(30):                  # 最多等 6 秒出最终结果
        await asyncio.sleep(0.2)
        if (getattr(prov, "text", "") or "").strip():
            break
    conn.stop_event.set()
    await asyncio.sleep(0.3)

    text_out = getattr(prov, "text", "") or ""
    print("-" * 70)
    got = (text_out or "").strip()
    if got:
        print(f"  ✅ 识别结果: {got}")
    else:
        print("  ⚠️ 没拿到文本（看上面日志是否有 code / 错误）")
    return got


def main():
    if len(sys.argv) > 1:
        wav = Path(sys.argv[1])
    else:
        cand = sorted((ROOT / "tmp" / "asr_bench").glob("*专有名词*.wav"))
        if not cand:
            print("没找到测试音频，先跑 tools/asr_bench.py 生成，或指定 wav 路径")
            return
        wav = cand[0]
        print(f"（自动选中 {wav.name}）")
    if not wav.exists():
        print(f"文件不存在: {wav}")
        return
    asyncio.run(run(wav))


if __name__ == "__main__":
    main()
