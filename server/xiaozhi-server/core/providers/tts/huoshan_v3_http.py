"""huoshan_v3_http.py —— 火山引擎「语音合成大模型 / 豆包语音合成2.0」v3 HTTP 通道

★ 为什么不用现成的 huoshan_double_stream（WS 双向流式）：
  1. 那个 provider 调 `check_model_key()`（v1 老接口，用 access_token 鉴权），
     而 **v3 根本不用 access_token** → 直接抛
     `TypeError: argument of type 'int' is not iterable`，
     表现为服务端日志一行「实例化组件失败」，然后**所有播报都被判成"设备离线"**。
  2. WS 通道握手头是 `X-Api-App-Key` + `X-Api-Access-Key` → 实测 **HTTP 401**；
     换成 `x-api-key` 能连上，但音频走二进制帧，要另写一套帧处理。
  3. v3 HTTP unidirectional 用 `x-api-key` + 分块 JSON 流，**已实测稳定出音**。
     代价是不能流式首包，但我们的链路是"分句合成 → 逐句下发"，不依赖流式。

★ 三个决定成败的细节（踩过才知道）：
  1. 鉴权头**只发 `x-api-key: <APPID>`**，不发 access_token
  2. **resource_id 必须和音色匹配**，否则 code=55000000：
     `_moon/_mars` 音色 → volc.service_type.10029
     `_uranus` 音色      → seed-tts-2.0
  3. 响应是**分块 JSON 流**（一行一个 JSON），整块 json.loads 会报
     "Extra data: line 2"，必须逐行解析再把 data 拼起来
"""
import asyncio
import base64
import importlib.util
import json
import os
import uuid as _uuid
from pathlib import Path

import requests

from config.logger import setup_logging
from core.providers.tts.base import TTSProviderBase

TAG = __name__
logger = setup_logging()

URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
RID_MEGA = "volc.service_type.10029"   # 语音合成1.0（_moon/_mars 音色）
RID_SEED = "seed-tts-2.0"              # 豆包语音合成2.0（_uranus 音色）

# 额度账本（bridge/quota.py）——懒加载，失败不阻塞语音
# ⚠️ 本文件路径 core/providers/tts/x.py → parents[5] 才是项目根
#   （别和 plugins_func/functions/*.py 的 parents[4] 混，那边层级不同）
_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_QUOTA = None


def _quota():
    global _QUOTA
    if _QUOTA is not None:
        return _QUOTA
    try:
        env = os.environ.get("XZ_BRIDGE_DIR")
        base = Path(env) if env else (_PROJECT_ROOT / "bridge")
        p = base / "quota.py"
        if not p.exists():
            return None
        spec = importlib.util.spec_from_file_location("xz_bridge_quota", str(p))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _QUOTA = mod
    except Exception as e:
        logger.bind(tag=TAG).debug(f"额度模块不可用: {e}")
        _QUOTA = None
    return _QUOTA


def _auto_rid(speaker: str) -> str:
    """按音色后缀猜 resource_id。显式配了 resource_id 就用配置值。"""
    s = (speaker or "").lower()
    if "uranus" in s:
        return RID_SEED
    return RID_MEGA


class TTSProvider(TTSProviderBase):
    TTS_PARAM_CONFIG = [
        ("ttsVolume", "volume_ratio", 0.1, 3, 1.0, lambda v: round(float(v), 2)),
        ("ttsRate", "speed_ratio", 0.2, 3, 1.0, lambda v: round(float(v), 2)),
        ("ttsPitch", "pitch_ratio", 0.1, 3, 1.0, lambda v: round(float(v), 2)),
    ]

    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        # ⚠️ v3 的 appid 是 UUID 形态（c105d0ce-...），绝不能 int() 转换
        #    （上游 doubao.py 就是 int(appid)，那是给数字 appid 用的）
        self.appid = str(config.get("appid") or "").strip()
        self.access_token = str(config.get("access_token") or "").strip()
        self.speaker = config.get("private_voice") or config.get("speaker") or config.get("voice")
        if not self.speaker:
            raise ValueError("huoshan_v3_http: 缺少 speaker 配置")
        rid = config.get("resource_id")
        # 没配 resource_id 就按音色后缀自动挑，省得换音色时忘了改
        self.resource_id = str(rid).strip() if rid else _auto_rid(self.speaker)
        self.api_url = config.get("api_url") or URL
        self.sample_rate = int(config.get("sample_rate", 24000) or 24000)
        self.audio_file_type = config.get("format", "mp3")
        self.timeout = int(config.get("timeout", 60) or 60)

        sr = config.get("speed_ratio", "1.0")
        vr = config.get("volume_ratio", "1.0")
        pr = config.get("pitch_ratio", "1.0")
        self.speed_ratio = float(sr) if sr else 1.0
        self.volume_ratio = float(vr) if vr else 1.0
        self.pitch_ratio = float(pr) if pr else 1.0
        self._apply_percentage_params(config)

        # ---- 额度 / 双通道（2026-10-03 主人要求：两个版本都接通，2.0 优先，用完自动降 1.0）----
        # auto_channel=true 时，每次合成都问一次额度账本：2.0 还有额度就用 2.0，没了就落到 1.0
        self.auto_channel = bool(config.get("auto_channel", True))
        self.speaker_by_channel = {
            "2.0": config.get("speaker_v2") or self.speaker,   # 留空则用 quota.CHANNELS 默认值
            "1.0": config.get("speaker_v1"),
        }
        self._last_channel = None

        # ---- 发音词典（2026-10-03）：修正专有名词念错 ----
        # 每行一条 "原词/(拼音音节)"，最长 9 字；认不准就别写，念错比不念更糟。
        self.pron_dict = self._load_pronunciation(config.get("pronunciation_file"))
        if self.pron_dict:
            logger.bind(tag=TAG).info(
                f"发音词典已加载 {len(self.pron_dict)} 条（仅豆包2.0通道生效）")

        if not self.appid:
            raise ValueError("huoshan_v3_http: 缺少 appid")

    @staticmethod
    def _load_pronunciation(path=None):
        """读 bridge/pronunciation.txt（相对项目根）。返回 ["词/(x)", ...]。"""
        try:
            if not path:
                env = os.environ.get("XZ_BRIDGE_DIR")
                base = Path(env) if env else (_PROJECT_ROOT / "bridge")
                path = base / "pronunciation.txt"
            p = Path(path)
            if not p.exists():
                return []
            out = []
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "/" not in line:
                    continue
                word, _, fix = line.partition("/")
                word, fix = word.strip(), fix.strip()
                if word and fix and len(word) <= 9:
                    out.append(f"{word}/{fix}")
            return out
        except Exception as e:
            logger.bind(tag=TAG).debug(f"发音词典加载失败: {e}")
            return []

    # ---------------- 通道选择（额度降级） ----------------

    def _resolve_channel(self):
        """返回 (resource_id, speaker, channel_name)。"""
        q = _quota()
        if not self.auto_channel or q is None:
            return self.resource_id, self.speaker, "固定"
        try:
            ch = q.pick_channel()
        except Exception:
            ch = None
        if not ch:
            # 两个额度都满了 —— 火山会自动转按量计费（扣账户余额），仍按配置走
            return self.resource_id, self.speaker, "超额(按量计费)"
        meta = q.CHANNELS.get(ch, {})
        rid = meta.get("rid") or self.resource_id
        spk = self.speaker_by_channel.get(ch) or meta.get("speaker") or self.speaker
        if ch != self._last_channel:
            logger.bind(tag=TAG).info(
                f"切换到语音合成 {ch} 通道：rid={rid} speaker={spk}（{q.describe(ch)}）")
            self._last_channel = ch
        return rid, spk, ch

    # ---------------- 合成 ----------------

    def _build_body(self, text: str, speaker: str, channel: str = "") -> dict:
        # speed_ratio 是倍率（1.0=原速），火山要的是百分比（0=原速，±100）
        speech_rate = int(round((self.speed_ratio - 1.0) * 100))
        loudness_rate = int(round((self.volume_ratio - 1.0) * 100))
        pitch = int(round((self.pitch_ratio - 1.0) * 100))
        additions = {
            "disable_markdown_filter": True,
            "enable_language_detector": True,
            "disable_default_bit_rate": True,
        }
        if pitch:
            additions["post_process"] = {"pitch": max(-12, min(12, pitch))}
        # ★ 发音词典只对「豆包语音合成2.0」音色有效，切到 1.0 时必须撤掉（会报错）
        if self.pron_dict and channel != "1.0":
            additions["pronunciation_dict"] = {"tone": list(self.pron_dict)}
        return {"req_params": {
            "text": text,
            "speaker": speaker,
            "additions": json.dumps(additions, ensure_ascii=False),
            "audio_params": {
                "format": self.audio_file_type,
                "sample_rate": self.sample_rate,
                "speech_rate": max(-50, min(100, speech_rate)),
                "loudness_rate": max(-50, min(100, loudness_rate)),
            },
        }}

    def _synth_blocking(self, text: str) -> bytes:
        rid, speaker, ch = self._resolve_channel()
        hdr = {
            "x-api-key": self.appid,          # ★ 只发 appid，不发 token
            "X-Api-Resource-Id": rid,
            "X-Api-Request-Id": _uuid.uuid4().hex,   # 必填
            # ★ 让火山在响应里回报精确计费字符数（usage.text_words），额度账本靠它记账
            "X-Control-Require-Usage-Tokens-Return": "*",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
        }
        r = requests.post(self.api_url,
                          json.dumps(self._build_body(text, speaker, ch), ensure_ascii=False).encode("utf-8"),
                          headers=hdr, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"火山 v3 HTTP {r.status_code}: {r.text[:160]}")
        # ★ 分块 JSON 流：一行一个 JSON 对象，data 字段拼起来才是完整音频
        b64_parts, errs, used_chars = [], [], 0
        for line in r.content.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("data"):
                b64_parts.append(j["data"])
            code = j.get("code")
            # 20000000 = OK（这是正常结束帧，不是错误）
            if code not in (0, "0", None, 20000000, "20000000"):
                errs.append(f"{code} {str(j.get('message'))[:120]}")
            u = j.get("usage") or {}
            if isinstance(u, dict) and u.get("text_words"):
                used_chars = int(u["text_words"])
        # ---- 记账 ----
        if used_chars:
            q = _quota()
            if q is not None:
                try:
                    info = q.add_usage(ch, used_chars)
                    if info and info["percent"] <= 20:
                        logger.bind(tag=TAG).info(
                            f"[额度] {info['name']} 剩余 {info['remaining']}/{info['quota']} 字符")
                except Exception as e:
                    logger.bind(tag=TAG).debug(f"额度记账失败: {e}")
        if not b64_parts:
            raise RuntimeError("火山 v3 没返回音频: " + ("; ".join(errs[:1]) or "空响应"))
        return base64.b64decode("".join(b64_parts).replace("\n", "").replace("\r", ""))

    async def text_to_speak(self, text, output_file):
        """base.py 有两种调用方式，这里必须都兼容：
             audio_bytes = await self.text_to_speak(text, None)      ← 要返回 bytes
             await self.text_to_speak(text, tmp_file)                 ← 顺带落盘
           ⚠️ 2026-10-03 踩过：只写文件不返回 bytes → base 拿到 None，
              日志刷「语音生成失败N次: expected str, bytes or os.PathLike object, not int」，
              音频探针 0 字节，服务端却完全看不出是哪一步坏的。
        """
        raw = await asyncio.to_thread(self._synth_blocking, text)
        if not raw:
            raise RuntimeError("火山 v3 返回空音频")
        if output_file:
            await asyncio.to_thread(self._write_file, output_file, raw)
        return raw

    @staticmethod
    def _write_file(path: str, data: bytes):
        with open(path, "wb") as f:
            f.write(data)

    async def close(self):
        # HTTP 无长连接，无需清理
        return
