# -*- coding: utf-8 -*-
"""
active-notify（主动播报）+ bridge-watcher（取件）+ reminder（定时提醒）
=====================================================================

本文件是**自研增量插件**，一个文件干三件事，都挂在同一个常驻服务里：

1. **主动播报**（8004）：外部 POST 一句话，后台直接合成并推给设备。
2. **取件 watcher**：盯 `bridge/done/`，一有完成的任务就主动开口播报，
   不用等主人先说话。（阶段一云方案只能"搭顺风车"，自建后台才能真主动。）
3. **定时提醒**：到点主动开口，是"助理感"的最低成本体现。

为什么放在这里
--------------
自建后台的进程是**长期常驻**的（由用户启动的管理台托管，不会被回收），
而 `bridge/` 那份 MCP 桥是"设备开口才跑"的请求-响应式。
要"主动"，必须挂在常驻进程上——所以做在这里，不另起进程。

上游代码一处不改
----------------
连接表用运行时包装 `ConnectionHandler.handle_connection` 拿到；
HTTP 走独立端口 8004。唯一在上游动的，是 `app.py` 里两行
导入 + 一次 `install_notify(config)`（见 `tools/notify_patch_app.py`）。

接口
----
    POST /notify          {"device_id": "84:c7:bb:58:17:44"?, "text": "要念的话"}
    GET  /notify/status   在线设备
    POST /remind          {"text": "...", "at": "08:00", "daily": true}
                          {"text": "...", "at": "2026-10-03T09:00"}      # 一次性
    GET  /remind/list     当前提醒列表
    POST /remind/delete   {"id": "..."}

    device_id 可省略，省略时广播给在线设备。

配置（config.yaml 顶层可选，都有默认值）
----------------------------------------
    notify:
      port: 8004
      bridge_dir:            # 默认自动 = <项目根>/bridge
      poll_interval: 5       # 取件轮询秒数
      announce_enabled: true
"""

import asyncio
import json
import os
import re
import time                     # ★ 2026-10-04：僵尸回收要按入队时刻算，模块级导入
from datetime import datetime, timedelta
from pathlib import Path

from aiohttp import web

from core import connection as _connection_mod
from core.connection import ConnectionHandler
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

# device_id -> ConnectionHandler
REGISTRY: dict = {}
# 持有 asyncio 任务引用，避免被 GC
_TASKS: list = []

# ------------------------------------------------------------------ 路径
# core/notify_plugin.py -> [0]=core [1]=xiaozhi-server [2]=server [3]=<项目根>
_SERVER_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DATA_DIR = _SERVER_DIR / "data"
_REMINDER_FILE = _DATA_DIR / "reminders.json"


def _bridge_dir() -> Path:
    env = os.environ.get("XZ_BRIDGE_DIR")
    return Path(env) if env else (_PROJECT_ROOT / "bridge")


def _dirs():
    base = _bridge_dir()
    inbox, done, announced = base / "inbox", base / "done", base / "announced"
    for d in (inbox, done, announced):
        d.mkdir(parents=True, exist_ok=True)
    return inbox, done, announced


def _device_id_of(websocket) -> str:
    """从连接对象上取 device-id（header 优先，query 兜底）"""
    try:
        headers = dict(websocket.request.headers)
        did = headers.get("device-id")
        if did:
            return str(did)
    except Exception:
        pass
    try:
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(websocket.request.path).query)
        if "device-id" in q:
            return str(q["device-id"][0])
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------- 连接表

_orig_handle_connection = ConnectionHandler.handle_connection


async def _flush_queue_on_online(did: str):
    """设备刚上线：把断线期间攒下的话补播出去。"""
    await asyncio.sleep(2.5)  # 等连接握手完（hello/mcp 初始化）
    if not _queue:
        return
    pending = list(_queue)
    _queue.clear()
    _queue_save()
    for item in pending[-5:]:
        ok, info = speak(item.get("text", ""), did)
        if ok:
            logger.bind(tag=TAG).info(f"[补播] {item.get('tag', '')} -> {info}: {item.get('text','')[:40]}")
            await asyncio.sleep(2.0)
        else:
            _queue.append(item)  # 还没通，留着下次
            _queue_save()


async def _patched_handle_connection(self, websocket):
    """包装上游 handle_connection，进出时登记/注销连接表"""
    did = _device_id_of(websocket)
    REGISTRY[did] = self
    logger.bind(tag=TAG).info(f"设备上线: {did}（当前在线 {len(REGISTRY)}）")
    try:
        _TASKS.append(asyncio.create_task(_flush_queue_on_online(did)))
    except Exception as e:
        logger.bind(tag=TAG).error(f"启动补播任务失败: {e}")
    try:
        return await _orig_handle_connection(self, websocket)
    finally:
        if REGISTRY.get(did) is self:
            REGISTRY.pop(did, None)
        logger.bind(tag=TAG).info(f"设备下线: {did}（当前在线 {len(REGISTRY)}）")


# ---------------------------------------------------------------- 播报内核


# 离线排队：设备不在时先把话存下来，等它上线再补播（2026-10-03）
# 场景：后台先起、设备后开机；或设备重启的几秒钟窗口。
# 以前 speak 失败就丢弃 → 主人永远听不到；现在入队，上线即补。
_QUEUE_FILE = _DATA_DIR / "notify_queue.json"
_queue: list = []


def _queue_load():
    global _queue
    try:
        if _QUEUE_FILE.exists():
            _queue = json.loads(_QUEUE_FILE.read_text(encoding="utf-8")) or []
    except Exception:
        _queue = []


def _queue_save():
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _QUEUE_FILE.write_text(
            json.dumps(_queue[-50:], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"存播报队列失败: {e}")


def _conn_alive(conn) -> bool:
    """连接是否真的还活着。

    ⚠️ 2026-10-03 血泪：REGISTRY 里有 conn 不代表能说话。
    设备频繁重连时，旧 conn 的 stop_event 已被 set，
    而 base.py 的 tts_text_priority_thread 是 `while not stop_event.is_set()`，
    → 往这种 conn 投消息 = 石沉大海，服务端日志还一切正常（最坑的地方）。
    """
    try:
        se = getattr(conn, "stop_event", None)
        if se is not None and se.is_set():
            return False
        ws = getattr(conn, "websocket", None)
        if ws is None:
            return False
        # websockets：OPEN 时 close_code 为 None；state 在 14.x 里 OPEN=1
        if getattr(ws, "close_code", None) is not None:
            return False
        st = str(getattr(ws, "state", ""))
        if "CLOSED" in st.upper() or "CLOSING" in st.upper():
            return False
        return True
    except Exception:
        return False


_AUDIO_PROBE_ON = False
_AUDIO_STAT = {}  # sentence_id -> [包数, 字节数]


def _ensure_audio_probe():
    """包装 sendAudioMessage，记录**真实音频字节数**。

    用途：服务端日志长期以来一直打印「发送第一段语音 / 发送音频消息」，
    看起来一切正常，但设备一声不吭。这个探针能把「合成出 0 字节」和
    「发了但设备没收到」彻底分开。
    """
    global _AUDIO_PROBE_ON
    if _AUDIO_PROBE_ON:
        return
    _AUDIO_PROBE_ON = True
    try:
        import core.providers.tts.base as _base

        orig = getattr(_base, "sendAudioMessage", None)
        if orig is None:
            return

        def _wrapped(conn, sentence_type, audios, text, sentence_id=None):
            try:
                if isinstance(audios, (bytes, bytearray)):
                    n, nb = 1, len(audios)
                elif isinstance(audios, (list, tuple)):
                    n = len(audios)
                    nb = sum(len(x) for x in audios if isinstance(x, (bytes, bytearray)))
                else:
                    n, nb = 0, 0
                st = getattr(sentence_type, "value", sentence_type)
                key = str(sentence_id)
                cnt = _AUDIO_STAT.setdefault(key, [0, 0])
                cnt[0] += n
                cnt[1] += nb
                if st == "LAST":
                    logger.bind(tag=TAG).info(
                        f"[音频探针] 本次播报合计 {cnt[0]} 个包 / {cnt[1]} 字节"
                        + ("  ⚠️ 0 字节 = 没合成出来，设备不会出声" if cnt[1] <= 0 else "")
                    )
                    _AUDIO_STAT.pop(key, None)
            except Exception as e:
                logger.bind(tag=TAG).error(f"[音频探针] 记录失败: {e}")
            return orig(conn, sentence_type, audios, text, sentence_id)

        _base.sendAudioMessage = _wrapped
        logger.bind(tag=TAG).info("[音频探针] 已挂载")
    except Exception as e:
        logger.bind(tag=TAG).error(f"[音频探针] 挂载失败: {e}")


def _send_tts_state(conn, state: str, text=None) -> bool:
    """直接给设备推一条 tts 状态消息（走 conn 所在的事件循环）。

    ⚠️⭐ 2026-10-03 主动播报「一句都听不到」的**最终真根因**（前面几轮全被日志骗了）：

      固件 main/application.cc 里音频包是这样被接的：
          protocol_->OnIncomingAudio([this](packet) {
              if (device_state_ == kDeviceStateSpeaking) {   // ← 只有说话状态才解码
                  audio_service_.PushPacketToDecodeQueue(std::move(packet));
              }
          });
      而让设备切进 speaking 的那条 `{"type":"tts","state":"start"}`，
      在**正常对话**里是 send_stt_message() 顺手发的（用户说完 → STT → 服务端回 start
      → 设备切 speaking → 后面的音频包才有人要）。

      主动播报没有 STT 这一环 → **从来没人发过 start** → 设备一直停在 idle
      → 服务端这边「语音生成成功 / 发送第一段语音 / 发送音频消息」日志一应俱全，
        设备也确实收到了 sentence_start（所以串口会打印 `Application: << 文本`、
        屏幕上也有字，看起来"到了"），
        但真正带声音的 opus 包全被上面那个状态判断**静默丢弃** → 一声不吭。

    修法：播报前自己补一条 tts/start。
    """
    try:
        import asyncio
        import json

        loop = getattr(conn, "loop", None)
        ws = getattr(conn, "websocket", None)
        if loop is None or ws is None:
            return False
        msg = {
            "type": "tts",
            "state": state,
            "session_id": getattr(conn, "session_id", ""),
        }
        if text is not None:
            msg["text"] = text

        async def _go():
            await ws.send(json.dumps(msg, ensure_ascii=False))

        fut = asyncio.run_coroutine_threadsafe(_go(), loop)
        fut.result(timeout=1.5)
        return True
    except Exception as e:
        # 注意：超时异常 str(e) 是空字符串，必须打 type/repr 才看得见
        logger.bind(tag=TAG).debug(
            f"[播报] 预热 tts state={state} 未成功（sendAudioHandle 里还有兜底）: "
            f"{type(e).__name__}: {e!r}"
        )
        return False


def speak(text: str, device_id: str = None) -> tuple:
    """向在线设备主动播报一句话。返回 (ok, device_id_or_error)。"""
    text = (text or "").strip()
    if not text:
        return False, "empty text"

    _ensure_audio_probe()

    target = REGISTRY.get(device_id) if device_id else None
    if target is None and not device_id:
        did = next(iter(REGISTRY.keys()), None)
        target = REGISTRY.get(did) if did else None
        device_id = did
    if target is None:
        return False, "device offline"

    # 连接死了就别投了——投了也是丢，直接告诉调用方去排队
    if not _conn_alive(target):
        logger.bind(tag=TAG).info(
            f"[播报] 连接已失效(stop_event/close_code)，转排队: {text[:30]}"
        )
        return False, "stale connection"

    # ⚠️ 2026-10-03 真·根因（前面几轮全被它骗了）：
    #   这里曾经写成 `content_type = ContentType`（枚举**类**），
    #   而 base.py 里的判断是 `elif ContentType.TEXT == message.content_type:`，
    #   类 ≠ 成员，恒为 False → 文本**从来没被 append 进 tts_text_buff** →
    #   _process_remaining_text_stream() 合成 0 字节 → sendAudio(conn, []) 一个包都不发。
    #   最阴的是：服务端日志照样打印「发送第一段语音 / 发送音频消息: LAST」，
    #   看起来一切正常，设备却一声不吭。
    # 正解（与 core/connection.py 上游写法一致）：
    #   FIRST → ContentType.ACTION（只重置状态，不碰文本）
    #   MIDDLE → ContentType.TEXT（唯一会把文本塞进缓冲区的地方）
    #   LAST   → ContentType.ACTION（冲出剩余文本；若也用 TEXT 会被 append 两遍，播两次）
    try:
        from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO
    except Exception as e:
        logger.bind(tag=TAG).error(f"导入 TTS 数据结构失败: {e}")
        return False, "import error"

    # 另外几个必须同时满足的条件（缺一个就静默无声，且日志看不出来）：
    #   1. tts_one_sentence 只塞文本、从不发 LAST，短句凑不够分段就永远憋着 → 必须自己补 LAST
    #   2. base.py 有 `if message.sentence_id != self.conn.sentence_id: continue`
    #      → sentence_id 必须同步写回 conn，否则整条消息被丢弃
    #   3. 设备 idle 久了两个消化线程会退出 → 队列没人消费
    #   4. `if self.conn.client_abort: continue` → 上次被打断过，后续全被跳过
    tts = getattr(target, "tts", None)
    if tts is None:
        return False, "no tts provider"

    try:
        target.client_abort = False  # 清掉上次的打断标记，否则本次也被跳过
    except Exception:
        pass

    # 消化线程不在就重新拉起（open_audio_channels 内部会起两个线程）
    try:
        need_restart = False
        for attr in ("tts_priority_thread", "audio_play_priority_thread"):
            th = getattr(tts, attr, None)
            if th is None or not th.is_alive():
                need_restart = True
                break
        if need_restart:
            tts.open_audio_channels(target)
            logger.bind(tag=TAG).info("TTS 消化线程已重启（设备 idle 期间退出了）")
    except Exception as e:
        logger.bind(tag=TAG).error(f"重启 TTS 线程失败: {e}")

    try:
        import uuid

        # ★ 提前把设备预热到 speaking，别让第一批音频包撞在 idle 上
        #   （固件只在 kDeviceStateSpeaking 时才解码音频包，详见 _send_tts_state 注释）
        #   真正兜底在 core/handle/sendAudioHandle.py 的 FIRST 分支里（那里本来就在
        #   conn 的事件循环内，一定能发出去），这里只是尽量早一点。
        _send_tts_state(target, "start")
        try:
            import time as _time

            _time.sleep(0.15)
            target.client_is_speaking = True
        except Exception:
            pass

        sid = uuid.uuid4().hex
        target.sentence_id = sid  # 必须同步，否则被 sentence_id 过滤掉
        q = tts.tts_text_queue
        q.put(TTSMessageDTO(sid, SentenceType.FIRST, ContentType.ACTION, None))
        q.put(TTSMessageDTO(sid, SentenceType.MIDDLE, ContentType.TEXT, text))
        q.put(TTSMessageDTO(sid, SentenceType.LAST, ContentType.ACTION, None))
        logger.bind(tag=TAG).info(
            f"[播报] 已投递 FIRST/MIDDLE(TEXT)/LAST sid={sid[:8]}: {text[:40]}"
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"投递播报失败: {type(e).__name__}: {e}")
        return False, str(e)

    return True, device_id


def enqueue(text: str, tag: str = "") -> int:
    """设备离线时把话排队，上线后自动补播。返回队列长度。"""
    text = (text or "").strip()
    if not text:
        return len(_queue)
    _queue.append(
        {"text": text, "tag": tag, "at": datetime.now().isoformat(timespec="seconds")}
    )
    _queue_save()
    return len(_queue)


def _runtime_cfg() -> dict:
    """读 data/.config.yaml 的 runtime 段（读失败返回空 dict，绝不抛）。"""
    try:
        from pathlib import Path
        import yaml
        p = Path(__file__).resolve().parents[1] / "data" / ".config.yaml"
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return (yaml.safe_load(f) or {}).get("runtime") or {}
    except Exception:
        pass
    return {}


def _notify_cfg() -> dict:
    """读 data/.config.yaml 的 notify 段（读失败返回空 dict，绝不抛）。"""
    try:
        from pathlib import Path
        import yaml
        p = Path(__file__).resolve().parents[1] / "data" / ".config.yaml"
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return (yaml.safe_load(f) or {}).get("notify") or {}
    except Exception:
        pass
    return {}


def _hhmm_to_min(v, default_min: int) -> int:
    """'23:00' → 1380。解析失败就用默认值（宁可用默认，也别让整个播报链路挂掉）。

    ⚠️ 必须兼容 int：YAML 1.1 会把裸写的 `23:00` 当**六十进制**解析成 1380，
    `07:00` 变成 420（不是字符串！）。所以管理台保存时虽然会加引号，
    但万一有人在文件里手写无引号版本，这里也得认——否则静默时段会悄悄跑偏。
    """
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        iv = int(v)
        if 0 <= iv < 24 * 60:
            return iv
        return default_min
    try:
        h, m = str(v or "").strip().split(":", 1)
        return int(h) * 60 + int(m)
    except Exception:
        return default_min


def _in_quiet_hours(now=None) -> bool:
    """当前是否处于「免打扰时段」（2026-10-04 主人要求）。

    语义：**只拦主动播报**（提醒 / 任务完成 / 额度告警），
    正常对话（你问它答）根本不走 speak_or_queue，不受影响。

    跨午夜：起点 23:00、终点 07:00 这种「起点 > 终点」的写法表示跨过零点，
    判据是 `now >= 起点 or now < 终点`，别写成 `起点 <= now <= 终点`（那会永远 False）。

    ⚠️ 起止相同视为配置无效 → 返回 False。
    否则用户手滑填成 23:00/23:00 会变成全天静默，这是最难查的一类故障
    （"我什么都没收到"但日志一切正常）。
    """
    cfg = _notify_cfg()
    if not cfg.get("quiet_hours_enabled"):
        return False
    from datetime import datetime
    d = now or datetime.now()
    cur = d.hour * 60 + d.minute
    s = _hhmm_to_min(cfg.get("quiet_hours_start"), 23 * 60)
    e = _hhmm_to_min(cfg.get("quiet_hours_end"), 7 * 60)
    if s == e:
        return False
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e


def _in_todo_speak_window(now=None) -> bool:
    """待办巡检专用的「可播时段」（2026-10-04 凌晨 3 点血的教训）。

    背景：待办「下午去打羽毛球」due=今天15:00，`needs_remind(within_hours=12)`
    把「12 小时内到期」都算该提醒 → 凌晨 03:01 就念「今天 15:00 该做了」，
    把主人从梦里吵醒。**预告窗口本身没错，错在没有时段下限。**

    与 `_in_quiet_hours` 的区别（别合并）：
      - 免打扰：**默认关**，主人自己决定要不要静音，管所有主动播报
      - 本函数：**默认开**，只管待办巡检一件事
    两者是「或」的关系：任一判定为静默，就不播。

    默认 07:00–22:00（起点 07:00 是为了放行 07:00–09:00 的早间汇总）。

    ⚠️ 窗口外**不打 reminded 戳**——否则这条待办会被当成"已提醒过"，
    白天再也不会念，等于消息丢了。跳过即可，下一轮自然接上。
    """
    cfg = _notify_cfg()
    from datetime import datetime
    d = now or datetime.now()
    cur = d.hour * 60 + d.minute
    s = _hhmm_to_min(cfg.get("todo_speak_start"), 7 * 60)
    e = _hhmm_to_min(cfg.get("todo_speak_end"), 22 * 60)
    if s == e:
        return True          # 配成一样 = 没配，退回全天可播
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e


def speak_or_queue(text: str, tag: str = "") -> tuple:
    """先试播；设备不在就排队。返回 (ok, info)。

    ★ 2026-10-03 主人要求：主动搭话时先有个声响，别直接说话显得生硬。
      这里加一个可配置的前缀（「嗨，」之类）：
      - 走本函数的**全是主动播报**（提醒 / 任务完成 / 额度告警 / 巡检），
        对话回复不走这里，所以不会影响正常问答。
      - 用**文本前缀**而不是单独播一个音效：省一次合成往返，时序更稳，
        额度也只多 2 个字符。
      - 前缀为空字符串就完全不要这个声响。
    """
    try:
        pre = _runtime_cfg().get("active_greeting", "")
    except Exception:
        pre = ""
    if pre and tag and not str(text).startswith(str(pre)):
        text = f"{pre}{text}"

    # ★ 2026-10-04 免打扰时段：这段时间里**不出声**，但消息不丢 —— 入队，
    #   等白天天亮了由 _drain_queue 补播（比"半夜念一句你又听不见"有用得多）。
    if _in_quiet_hours():
        n = enqueue(text, tag)
        logger.bind(tag=TAG).info(
            f"[免打扰] 静默期内不播，已入队待补播（队内 {n} 条）：{str(text)[:30]}"
        )
        return False, f"quiet({n})"

    ok, info = speak(text)
    if ok:
        return True, info
    n = enqueue(text, tag)
    return False, f"queued({n})"


async def _drain_queue():
    """常驻循环：设备上线后把排队的话补播出去。"""
    while True:
        try:
            # 免打扰时段一律不补播（否则设备在线，队列会被马上播掉，静默形同虚设）。
            # 只休眠不做事，等出了静默窗口自然恢复。
            if _in_quiet_hours():
                await asyncio.sleep(60)
                continue
            if _queue and REGISTRY:
                left = []
                for item in _queue:
                    ok, _ = speak(item.get("text", ""))
                    if ok:
                        logger.bind(tag=TAG).info(
                            f"[补播] tag={item.get('tag','')} {item.get('text','')[:40]}"
                        )
                    else:
                        left.append(item)
                _queue[:] = left
                _queue_save()
        except Exception as e:
            logger.bind(tag=TAG).error(f"补播异常: {e}")
        await asyncio.sleep(5)


def _broadcast(text: str) -> int:
    """给所有在线设备播报（一般只有一台）。返回成功台数。"""
    if not text.strip():
        return 0
    n = 0
    for did in list(REGISTRY.keys()):
        try:
            ok, _ = speak(text, did)
            n += 1 if ok else 0
        except Exception as e:
            logger.bind(tag=TAG).error(f"广播到 {did} 失败: {e}")
    return n


# ---------------------------------------------------------------- 取件 watcher


def _read_task(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    title, result = "", ""
    if "## 任务内容" in text:
        title = text.split("## 任务内容", 1)[1]
        if "## 执行结果" in title:
            title = title.split("## 执行结果", 1)[0]
        title = title.strip()
    if "## 执行结果" in text:
        result = text.split("## 执行结果", 1)[1].strip()
    return {"title": title, "result": result}


def _pending_results(limit: int = 3, max_chars: int = 90) -> list:
    """收集 done/ 里未播报的完成结果。**不**写标记——发送成功后才写。
    2026-10-03：主人嫌播报太长，max_chars 260 → 90，只念一句人话。"""
    _, done, announced = _dirs()
    out = []
    for f in sorted(done.glob("task-*.md")):
        tid = f.name[len("task-"):-3]
        if (announced / f"{tid}.mark").exists():
            continue
        d = _read_task(f)
        if not d["result"]:
            continue
        r = d["result"].replace("\n", " ").strip()
        if len(r) > max_chars:
            r = r[:max_chars] + "。详细内容在电脑上"
        out.append((tid, d["title"][:40], r))
        if len(out) >= limit:
            break
    return out


def _mark_announced(tid: str):
    _, _, announced = _dirs()
    try:
        (announced / f"{tid}.mark").write_text(
            datetime.now().isoformat(timespec="seconds"), encoding="utf-8"
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"写播报标记失败 {tid}: {e}")


async def _watch_bridge(cfg: dict):
    """常驻循环：done/ 有结果 -> 主动开口 -> 写标记（发送成功才写，失败下次重试）"""
    interval = int(cfg.get("poll_interval", 2) or 2)  # 2026-10-03: 5 -> 2，完成播报更快
    # 给设备一点上线时间，避免服务刚起来时设备还没连上
    await asyncio.sleep(5)
    logger.bind(tag=TAG).info(f"取件 watcher 已启动（每 {interval}s 扫一次 {_bridge_dir() / 'done'}）")
    while True:
        try:
            if REGISTRY:
                for tid, title, result in _pending_results():
                    # 2026-10-03 主人要求：不要硬截断（内容会不完整），
                    #   改成"先总结成一句完整的话再播"，≤100 字；>120 字才兜底截断。
                    #   降级链：LLM 摘要 → 原文（宁可长也不说半句）→ 智能截断（优先断在句号）
                    text = result
                    summ = _load_summarizer()
                    if summ is not None:
                        try:
                            text = await summ.summarize(result, title)
                        except Exception as e:
                            logger.bind(tag=TAG).debug(f"摘要失败，用原文: {e}")
                            text = result
                    ok, info = speak_or_queue(text, tag=f"done:{tid}")
                    if ok:
                        _mark_announced(tid)
                        logger.bind(tag=TAG).info(f"[取件播报] {tid} -> {info}: {text[:60]}")
                    else:
                        # 已入队，标记已播，避免上线后重复念
                        _mark_announced(tid)
                        logger.bind(tag=TAG).info(f"[取件] {tid} 设备离线已入队，稍后补播")
        except Exception as e:
            logger.bind(tag=TAG).error(f"取件 watcher 异常: {e}")
        await asyncio.sleep(interval)


# ---------------------------------------------------------------- 本机执行器
# inbox/ 有新任务 -> 交给 core.pc_agent 用 DeepSeek + 本机工具真正干完 -> 落到 done/
# 这一步补的是"谁去执行"：原来靠 WorkBuddy 的定时任务（最快也要等下次触发），
# 现在由常驻后台自己干，时延从最长 1 小时降到几秒~几十秒。

_EXEC_SEM = None
_EXEC_RUNNING = set()
_EXEC_START_TIME = {}          # ★ 2026-10-04：记每个任务的入队时刻，给僵尸回收兜底用


_last_board_sig = [None]
_TODO_GREETED = [None]      # 记录"今天早上已经汇总播报过"，防重复
_TODO_SKIP_LOGGED = [None]  # 记录"今天已经打过一次跳过日志"，避免每 10 分钟刷一条


def _load_todo():
    """懒加载 bridge/todo.py；失败返回 None（绝不抛异常拖垮后台）。"""
    try:
        import importlib.util
        base = _bridge_dir()
        path = base / "todo.py"
        if not path.exists():
            return None
        spec = importlib.util.spec_from_file_location("xz_bridge_todo_watch", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        logger.bind(tag=TAG).debug(f"待办模块不可用: {e}")
        return None


def _human_due(td: dict) -> str:
    """把 due 说成人话。"""
    due = (td.get("due") or "").strip()
    if not due:
        return "没定时间"
    day, _, hm = due.partition(" ")
    from datetime import datetime, timedelta
    today = datetime.now().date()
    try:
        d = datetime.strptime(day, "%Y-%m-%d").date()
    except Exception:
        return due
    diff = (d - today).days
    base = {0: "今天", 1: "明天", 2: "后天"}.get(diff, due)
    if diff < 0:
        base = f"已经过期 {-diff} 天"
    return f"{base} {hm}" if hm else base


_QUOTA_LAST_CH = [None]


def _load_quota():
    """懒加载 bridge/quota.py；失败返回 None。"""
    try:
        import importlib.util
        path = _bridge_dir() / "quota.py"
        if not path.exists():
            return None
        spec = importlib.util.spec_from_file_location("xz_bridge_quota_watch", str(path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        logger.bind(tag=TAG).debug(f"额度模块不可用: {e}")
        return None


async def _quota_watch_loop(cfg: dict):
    """★ 火山语音额度守护（2026-10-03）

    主人要求：1.0 和 2.0 都接通，**优先用 2.0，用完自动降 1.0**，
    并且**快用完时主动提醒**（要等额度用完再去买就晚了）。

    这里只负责"开口说话"，通道切换本身在 provider 里已经自动完成了
    （huoshan_v3_http._resolve_channel → quota.pick_channel）。
    """
    interval = int(cfg.get("quota_poll_interval", 1800) or 1800)
    await asyncio.sleep(45)          # 等服务起稳
    q = _load_quota()
    if q is None:
        logger.bind(tag=TAG).info("额度巡检未启动（bridge/quota.py 不存在）")
        return
    logger.bind(tag=TAG).info(f"额度巡检已启动（每 {interval // 60} 分钟检查一次）")

    # ⚠️ 直接 await，不要 asyncio.create_task —— 那样外层协程立刻返回，
    #    子任务没有强引用可能被 GC 回收，表现为"日志打了但巡检没在跑"
    while True:
            try:
                ch = q.pick_channel()
                # 1) 通道发生变化（2.0 → 1.0）→ 告诉主人一声
                if ch != _QUOTA_LAST_CH[0]:
                    first = _QUOTA_LAST_CH[0] is None
                    _QUOTA_LAST_CH[0] = ch
                    if not first:
                        if ch is None:
                            speak_or_queue(
                                "语音额度两个通道都用完了，之后会按用量扣账户余额，"
                                "你方便的时候去控制台看看要不要充值。", tag="quota-out")
                        else:
                            info = q.remaining(ch)
                            speak_or_queue(
                                f"语音合成2点0的额度用完了，我已经自动切到1点0，"
                                f"声音会稍微不一样，1点0还剩{info['remaining']}字符。",
                                tag="quota-switch")
                # 2) 剩余低于阈值 → 提醒（quota 内部有 6 小时冷却，不会反复念）
                for info in q.need_alert():
                    speak_or_queue(
                        f"提醒一下，语音合成{info['channel']}的免费额度只剩"
                        f"{info['remaining']}字符了，大概还能说{info['remaining'] // 30}句，"
                        f"不够的话记得去控制台充值。", tag="quota-low")
                    q.mark_alerted(info["channel"])
            except Exception as e:
                logger.bind(tag=TAG).debug(f"额度巡检跳过: {e}")
            await asyncio.sleep(interval)

    asyncio.create_task(_loop())


async def _distill_daily_loop(cfg: dict):
    """★ 每日凌晨记忆蒸馏（2026-10-03 主人重点要求）

    「每天凌晨后台自动整理汇总当天有记忆有价值的记忆，完善到 WorkBuddy 记忆中，
      这样 WorkBuddy 与小智 都能够同时优化、完善增强后续记忆内容」

    实测：一天 35 条素材 → 抽出 23 条新记忆（3 条去重、2 条冲突取代），
    其中 14 条判为思维层，16 行写进 WorkBuddy 的 MEMORY.md。
    幂等：bridge/distill_state.json 记已处理日期，同一天重复跑不会产生重复记忆。
    """
    hour = int(cfg.get("distill_hour", 3) or 3)
    minute = int(cfg.get("distill_minute", 10) or 10)
    await asyncio.sleep(60)
    logger.bind(tag=TAG).info(f"记忆蒸馏已挂载（每天 {hour:02d}:{minute:02d} 跑一次）")
    last_done = None
    while True:
        try:
            now = datetime.now()
            if now.hour == hour and now.minute >= minute and last_done != now.date():
                last_done = now.date()
                import importlib.util
                p = _bridge_dir() / "distill.py"
                if not p.exists():
                    logger.bind(tag=TAG).warning("distill.py 不存在，跳过蒸馏")
                else:
                    spec = importlib.util.spec_from_file_location("xz_distill", str(p))
                    m = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(m)
                    r = m.run_once()          # 内部加锁且幂等，不会和重复触发打架
                    if r.get("skipped"):
                        logger.bind(tag=TAG).info(f"记忆蒸馏跳过（{r.get('reason')}）")
                    else:
                        logger.bind(tag=TAG).info(
                            f"记忆蒸馏完成 {r.get('day')}：候选 {r.get('candidates')} → "
                            f"新增 {r.get('added')} / 重复 {r.get('duplicated')} / "
                            f"思维层 {r.get('l3')} / 写入 WorkBuddy {r.get('wb_lines')} 行")
                        if r.get("added") or r.get("wb_lines"):
                            speak_or_queue(
                                f"记忆蒸馏完成，新增{r.get('added')}条记忆，"
                                f"其中{r.get('l3')}条进思维层。", tag="distill-done")
        except Exception as e:
            logger.bind(tag=TAG).debug(f"蒸馏循环跳过: {e}")
        await asyncio.sleep(600)


async def _todo_watch_loop(cfg: dict):
    """★ 主人的最终目标：小智自己盯着没做完的事，到点主动开口。

    节奏（刻意保守，避免变吵闹）：
      - 每 10 分钟扫一次
      - todo.needs_remind() 内部保证同一条 4 小时内不重复念
      - 早 07:00-09:00 额外做一次"今天要做"汇总，一天只做一次
      - 只播最多 2 条，念完打 reminded 戳
    """
    interval = int(cfg.get("todo_poll_interval", 600) or 600)
    await asyncio.sleep(20)          # 别跟启动抢跑
    logger.bind(tag=TAG).info(f"待办巡检已启动（每 {interval // 60} 分钟扫一次）")
    from datetime import datetime
    while True:
        try:
            td = _load_todo()
            if td is not None:
                now = datetime.now()
                # 早间汇总，一天一次
                if 7 <= now.hour < 9 and _TODO_GREETED[0] != now.date().isoformat():
                    _TODO_GREETED[0] = now.date().isoformat()
                    today_items = td.due_today()
                    over = td.overdue()
                    if today_items or over:
                        parts = []
                        if over:
                            parts.append("过期的有" + "、".join(
                                f"{x['title']}" for x in over[:2]))
                        if today_items:
                            parts.append("今天有" + "、".join(
                                f"{x['title']}" for x in today_items[:3]))
                        txt = "早上好。" + "；".join(parts) + "。"
                        if len(txt) > 90:
                            txt = txt[:88] + "…"
                        speak_or_queue(txt, tag="todo-morning")
                        for x in (over + today_items)[:5]:
                            td.mark_reminded(x["id"])

                # 到点提醒（needs_remind 已做 4 小时去重）
                # ★ 2026-10-04：先过可播时段。凌晨念「今天 15:00 该做了」是把人吵醒，
                #   不是提醒。窗口外**不打 reminded 戳**，白天照常念，消息不丢。
                if not _in_todo_speak_window(now):
                    # 留痕：否则"该播的没播"和"巡检根本没跑"在日志上长得一模一样，
                    # 事后没法区分（2026-10-04 凌晨排查时就是吃了这个亏）。
                    if _TODO_SKIP_LOGGED[0] != now.date().isoformat():
                        _TODO_SKIP_LOGGED[0] = now.date().isoformat()
                        logger.bind(tag=TAG).info(
                            f"待办巡检：{now:%H:%M} 不在可播时段"
                            f"（{cfg.get('todo_speak_start', '07:00')}-"
                            f"{cfg.get('todo_speak_end', '22:00')}），跳过；不打已读戳，白天照常念"
                        )
                    need = []
                else:
                    need = td.needs_remind(
                        within_hours=int(cfg.get("todo_within_hours", 12) or 12)
                    )
                if need:
                    pick = need[:2]
                    say = "提醒一下，" + "；".join(
                        f"{x['title']}，{_human_due(x)}该做了" for x in pick)
                    if len(say) > 90:
                        say = say[:88] + "…"
                    speak_or_queue(say, tag="todo-due")
                    for x in pick:
                        td.mark_reminded(x["id"])
        except Exception as e:
            logger.bind(tag=TAG).debug(f"待办巡检跳过: {e}")
        await asyncio.sleep(interval)


def _maybe_refresh_board(inbox: Path, done: Path):
    """任务集合有变化才重写看板，避免每 3 秒白写一次文件。"""
    try:
        sig = (
            len(list(inbox.glob("task-*.md"))),
            len(list(done.glob("task-*.md"))),
            max([p.stat().st_mtime for p in list(inbox.glob("task-*.md"))
                 + list(done.glob("task-*.md"))] or [0]),
        )
    except Exception:
        return
    if sig == _last_board_sig[0]:
        return
    _last_board_sig[0] = sig
    try:
        from core.task_board import refresh

        refresh()
    except Exception as e:
        logger.bind(tag=TAG).debug(f"看板刷新跳过: {e}")


_STALE_RUNNING_SEC = 900          # 15 分钟还没回结果就当僵尸
_EXEC_RUNNING_MAX_SEC = 300       # ★ 正在执行的任务：5 分钟还没回结果也判定卡死（原为永久豁免）
_reaped: set = set()              # 已回收过的文件名，防止同一轮重复处理


# ★ 2026-10-04 主人要的「长任务先打个招呼」：
#   命中这些词的任务八成要跑一会儿。取件时就先说清「可能要久一点，你先忙别的」，
#   免得主人对着沉默的设备干等。
#   ⚠️ 取向：**宁可漏判也别误判** —— 多说一句"要花点时间"几乎没代价，
#      而让主人干等 3 分钟不知道有没有接单，代价很大。
_LONG_TASK_HINTS = (
    "排查", "查一下", "查查", "查一查", "为什么", "怎么回事", "查明", "诊断",
    "安装", "下载", "编译", "整理", "分析", "总结", "写一份", "生成一份", "写个",
    # ⚠️ 别放「所有 / 全部」这种太宽泛的词：实测会把
    #   「显示桌面（最小化所有窗口）」「关掉所有浏览器」这种 1 步原子操作也判成长任务，
    #   开口就说"要花点时间"很傻。要表达"量大"请用「批量」这种明确的词。
    "批量", "对比", "调研", "检查", "统计", "进度", "梳理",
    "搜索", "找一找", "找找", "翻一下", "盘点", "备份", "迁移",
)


def _looks_long_task(text: str) -> bool:
    """粗判这活是不是要跑一会儿。"""
    t = text or ""
    return any(k in t for k in _LONG_TASK_HINTS)


_NUDGE_SEC = [60]   # 长任务中途补话的阈值（配置 notify.long_task_nudge_sec 可改）


def _ack_task(head: str):
    """★ 任务取件瞬间播一句回执（2026-10-03）

    为什么需要：执行器是 pc_agent + 大模型真的在干活（查注册表、找开始菜单…），
    复杂任务实测要 3 分钟。这期间设备完全静默，主人会以为"没接单"。

    ★ 2026-10-04 分两种话术：
      - 预判要跑一会儿 → "可能要花点时间，你先忙别的，弄完我叫你"
      - 普通活         → "好的，正在处理：xxx"
    **这不等于结果，结果由取件 watcher 播。**
    """
    if not _ACK_SWITCH[0]:
        return
    try:
        m = re.search(r"##\s*任务内容\s*\n+(.+?)(?:\n##|\Z)", head, re.S)
        if not m:
            return
        body = m.group(1)
        title = " ".join(body.split())[:28]
        if not title:
            return
        if _looks_long_task(body):
            speak_or_queue(
                f"收到，{title}。这个可能要花点时间，你先忙别的，弄完我叫你。",
                tag="task-ack-long")
        else:
            speak_or_queue(f"好的，正在处理：{title}", tag="task-ack")
    except Exception as e:
        logger.bind(tag=TAG).debug(f"任务回执跳过: {e}")


_ACK_SWITCH = [True]              # 运行时开关（配置 notify.task_ack_enabled 可关）
_SUMMARIZER = None


def _load_summarizer():
    """懒加载 tools/speech_summary.py（LLM 摘要器）。失败返回 None。"""
    global _SUMMARIZER
    if _SUMMARIZER is not None:
        return _SUMMARIZER or None
    try:
        import importlib.util
        # core/notify_plugin.py → parents[3] = 项目根
        root = Path(__file__).resolve().parents[3]
        p = root / "tools" / "speech_summary.py"
        if not p.exists():
            _SUMMARIZER = False
            return None
        spec = importlib.util.spec_from_file_location("xz_speech_summary", str(p))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SUMMARIZER = mod
        logger.bind(tag=TAG).info("播报摘要器已加载（任务结果会先总结再播）")
        return mod
    except Exception as e:
        logger.bind(tag=TAG).warning(f"播报摘要器加载失败，将直接播原文: {e}")
        _SUMMARIZER = False
        return None


def _reap_stale_tasks(inbox: Path, done: Path):
    """★ 回收卡在 running 的僵尸任务（2026-10-03 修）

    成因：任务被取走时状态改成 running，如果
      ① 执行器抛异常 / ② 服务重启（改配置、切换 TTS 都会重启）
      任务就永远停在这个状态 —— 因为主循环只认 `pending`，
      `if "- 状态：pending" not in head: continue` 会永远跳过它 → 永久卡死。

    症状：inbox 里躺着一个 running 的 task-*.md，几十小时不动，
         done 目录里 0 个 running（因为它压根没被移过去）。

    处理：超过阈值就判定失败、写明原因、移到 done，并让小智主动告知。
    """
    import time
    now = time.time()
    for f in list(inbox.glob("task-*.md")):
        if f.name in _reaped:
            continue
        if f.name in _EXEC_RUNNING:
            # ★ 2026-10-04：原来 running 被**永久豁免**回收，
            #   于是唯一的安全网恰好在最需要它的场景失效——任务卡住时它一直停在 running，
            #   回收永远跳过它（本函数上面 875 行自己都写着「→ 永久卡死」）。
            #   改成：running 用**更长的独立阈值**（按入队时刻算），该兜住时照样兜住。
            if (now - (_EXEC_START_TIME.get(f.name) or f.stat().st_mtime)) < _EXEC_RUNNING_MAX_SEC:
                continue
        try:
            head = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if "- 状态：running" not in head:
            continue
        # 真正在跑的用短阈值（300s）；孤儿 running（服务重启遗留）沿用长阈值（900s + mtime）
        limit = _EXEC_RUNNING_MAX_SEC if f.name in _EXEC_RUNNING else _STALE_RUNNING_SEC
        age = now - (_EXEC_START_TIME.get(f.name) or f.stat().st_mtime)
        if age < limit:
            continue
        _reaped.add(f.name)
        mins = int(age // 60)
        try:
            body = head.replace("- 状态：running", "- 状态：failed")
            if "## 执行结果" in body:
                body = body.split("## 执行结果")[0] + (
                    f"## 执行结果\n\n执行器中断或超时（已 running {mins} 分钟无结果），"
                    f"判定为失败。\n")
            (done / f.name).write_text(body, encoding="utf-8")
            f.unlink()
            logger.bind(tag=TAG).warning(
                f"回收僵尸任务 {f.name}（running 超过 {mins} 分钟）")
            speak_or_queue(
                f"有个任务卡住了，我判定它失败了：{f.stem[5:]}。", tag="task-stale")
        except Exception as e:
            logger.bind(tag=TAG).debug(f"回收僵尸任务失败: {e}")


async def _execute_inbox_loop(cfg: dict):
    """常驻循环：扫 inbox，把 pending 任务丢给本机执行器。"""
    global _EXEC_SEM, _NUDGE_SEC
    _NUDGE_SEC[0] = int(cfg.get("long_task_nudge_sec", 60) or 60)
    if _EXEC_SEM is None:
        _EXEC_SEM = asyncio.Semaphore(int(cfg.get("max_parallel_tasks", 2) or 2))

    interval = int(cfg.get("exec_poll_interval", 3) or 3)
    await asyncio.sleep(3)
    logger.bind(tag=TAG).info(
        f"任务执行器已启动（每 {interval}s 扫一次 {_bridge_dir() / 'inbox'}，由后台自己干完）"
    )
    while True:
        try:
            inbox, done, _ = _dirs()
            _reap_stale_tasks(inbox, done)
            _maybe_refresh_board(inbox, done)
            for f in sorted(inbox.glob("task-*.md")):
                if f.name in _EXEC_RUNNING:
                    continue
                try:
                    head = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                if "- 状态：pending" not in head:
                    continue  # running/done/failed 都不碰
                _EXEC_RUNNING.add(f.name)
                _EXEC_START_TIME[f.name] = time.time()   # ★ 给僵尸回收记入队时刻
                # ★ 2026-10-03 主人反馈「派给后台了但没任何回复」：
                #   实测是执行器认真跑（找遍整台电脑查软件）花了 3 分钟，
                #   期间设备一声不吭 → 主人以为没接。
                #   这里**取件瞬间就播一句回执**，让主人知道"收到了、正在干"。
                #   真正的结果仍由取件 watcher 在 done/ 落盘后播报。
                _ack_task(head)
                asyncio.create_task(_run_one_task(f))
                # ★ 跑太久还没完就中途补一句（配置 notify.long_task_nudge_sec，默认 60s）
                asyncio.create_task(_nudge_if_still_running(f, _NUDGE_SEC[0]))
        except Exception as e:
            logger.bind(tag=TAG).error(f"任务执行器异常: {e}")
        await asyncio.sleep(interval)


async def _nudge_if_still_running(path: Path, sec: int = 60):
    """★ 长任务跑过阈值还没完，中途补一句（2026-10-04）

    为什么：取件时那句回执管的是"开头"，但真跑上两三分钟时，
    主人不知道它是还在干还是已经死了 —— 中途补一句就踏实了。
    任务若已结束就不播（查 `_EXEC_RUNNING`）。
    """
    try:
        await asyncio.sleep(sec)
        if path.name in _EXEC_RUNNING:
            speak_or_queue("那个任务还在弄着，快好了，弄完我马上叫你。", tag="task-nudge")
    except Exception as e:
        logger.bind(tag=TAG).debug(f"长任务中途提示跳过: {e}")


async def _run_one_task(path: Path):
    try:
        async with _EXEC_SEM:
            from core.pc_agent import execute_task_file

            r = await asyncio.to_thread(execute_task_file, path)
            logger.bind(tag=TAG).info(
                f"[执行完成] {r.get('tid')} ok={r.get('ok')} "
                f"{r.get('ms')}ms 结果: {str(r.get('result'))[:80]}"
            )
    except Exception as e:
        logger.bind(tag=TAG).error(f"[执行失败] {path.name}: {e}")
    finally:
        _EXEC_RUNNING.discard(path.name)
        _EXEC_START_TIME.pop(path.name, None)
        try:
            from core.task_board import refresh

            refresh()  # 状态变了立刻刷看板
        except Exception:
            pass


# ---------------------------------------------------------------- 定时提醒

def _load_reminders() -> list:
    try:
        if _REMINDER_FILE.exists():
            return json.loads(_REMINDER_FILE.read_text(encoding="utf-8")) or []
    except Exception as e:
        logger.bind(tag=TAG).error(f"读提醒失败: {e}")
    return []


def _save_reminders(items: list):
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        _REMINDER_FILE.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.bind(tag=TAG).error(f"存提醒失败: {e}")


def _next_id(items: list) -> str:
    n = 1
    used = {it.get("id") for it in items}
    while f"r{n}" in used:
        n += 1
    return f"r{n}"


_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_at(at: str):
    """
    支持两种写法：
      "HH:MM"              -> 一次性（今天该时刻，已过则顺延到明天）；配合 daily=true 变成每天
      "YYYY-MM-DDTHH:MM"   -> 一次性绝对时间
      "YYYY-MM-DD HH:MM"   -> 同上（容错空格）
    返回 (datetime, is_clock) ；无效返回 (None, False)
    """
    at = (at or "").strip()
    m = _TIME_RE.match(at)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        now = datetime.now()
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        return t, True
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(at, fmt), False
        except ValueError:
            continue
    return None, False


async def _reminder_loop(cfg: dict):
    await asyncio.sleep(8)
    logger.bind(tag=TAG).info(f"定时提醒已启动（{_REMINDER_FILE}）")
    while True:
        try:
            items = _load_reminders()
            if items:
                now = datetime.now()
                changed = False
                for it in items:
                    if not it.get("enabled", True):
                        continue
                    fire_at, is_clock = _parse_at(it.get("at", ""))
                    if not fire_at:
                        continue
                    # "每天 HH:MM" 模式：用 next_at 作为下一次触发点
                    key = "next_at" if is_clock else "at"
                    try:
                        due = datetime.fromisoformat(it.get(key) or fire_at.isoformat())
                    except Exception:
                        due = fire_at
                    if now >= due:
                        # 2026-10-03：离线也照样"到点"，放进队列等上线补播，绝不再静默吞掉
                        ok, info = speak_or_queue(
                            f"提醒你：{it.get('text', '')}", tag=f"remind:{it['id']}"
                        )
                        if ok:
                            logger.bind(tag=TAG).info(f"[提醒] {it['id']} 已播报: {it.get('text')}")
                        else:
                            logger.bind(tag=TAG).info(
                                f"[提醒] {it['id']} 设备不在线，已入队待补播: {it.get('text')}"
                            )
                        if is_clock:
                            it["next_at"] = (due + timedelta(days=1)).isoformat()
                        else:
                            it["enabled"] = False
                        it["last_fired"] = now.isoformat(timespec="seconds")
                        changed = True
                if changed:
                    _save_reminders(items)
        except Exception as e:
            logger.bind(tag=TAG).error(f"提醒循环异常: {e}")
        await asyncio.sleep(15)


# ---------------------------------------------------------------- HTTP


def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    return resp


async def _handle_options(request):
    return _cors(web.Response(status=204))


async def _handle_status(request):
    return _cors(
        web.json_response(
            {
                "ok": True,
                "online": sorted(REGISTRY.keys()),
                "bridge_dir": str(_bridge_dir()),
                "reminders": len(_load_reminders()),
                "queued": len(_queue),  # 离线期间攒着待补播的话
            }
        )
    )


async def _handle_notify(request):
    try:
        data = await request.json()
    except Exception:
        return _cors(web.json_response({"ok": False, "error": "body must be json"}, status=400))

    text = (data.get("text") or "").strip()
    if not text:
        return _cors(web.json_response({"ok": False, "error": "text is required"}, status=400))

    did = data.get("device_id")
    ok, info = speak_or_queue(text, tag="http")
    if ok:
        logger.bind(tag=TAG).info(f"主动播报已下发 -> {info}: {text[:40]}")
        return _cors(web.json_response({"ok": True, "device_id": info, "text": text}))
    # 设备不在线：入队，等上线补播（返回 202 而不是 503，调用方不必重试）
    logger.bind(tag=TAG).info(f"主动播报已入队（设备离线，{info}）: {text[:40]}")
    return _cors(
        web.json_response(
            {"ok": True, "queued": True, "device_id": None, "text": text,
             "online": sorted(REGISTRY.keys())},
            status=202,
        )
    )


async def _handle_remind_add(request):
    try:
        data = await request.json()
    except Exception:
        return _cors(web.json_response({"ok": False, "error": "body must be json"}, status=400))

    text = (data.get("text") or "").strip()
    at = (data.get("at") or "").strip()
    if not text or not at:
        return _cors(web.json_response({"ok": False, "error": "text and at are required"}, status=400))

    fire_at, is_clock = _parse_at(at)
    if not fire_at:
        return _cors(
            web.json_response(
                {"ok": False, "error": "at 格式应为 HH:MM 或 YYYY-MM-DDTHH:MM"}, status=400
            )
        )

    items = _load_reminders()
    rid = _next_id(items)
    item = {
        "id": rid,
        "text": text,
        "at": at,
        "daily": bool(data.get("daily", is_clock)),
        "enabled": True,
        "created": datetime.now().isoformat(timespec="seconds"),
        "next_at": fire_at.isoformat(),
    }
    items.append(item)
    _save_reminders(items)
    logger.bind(tag=TAG).info(f"[提醒] 新增 {rid}: {text} @ {at} daily={item['daily']}")
    return _cors(web.json_response({"ok": True, "item": item}))


async def _handle_remind_list(request):
    return _cors(web.json_response({"ok": True, "items": _load_reminders()}))


async def _handle_remind_delete(request):
    try:
        data = await request.json()
    except Exception:
        return _cors(web.json_response({"ok": False, "error": "body must be json"}, status=400))
    rid = (data.get("id") or "").strip()
    items = _load_reminders()
    left = [it for it in items if it.get("id") != rid]
    if len(left) == len(items):
        return _cors(web.json_response({"ok": False, "error": f"id {rid} not found"}, status=404))
    _save_reminders(left)
    return _cors(web.json_response({"ok": True, "deleted": rid}))


async def _handle_diag(request):
    """诊断：打印每条连接的真实状态，并对设备发一帧裸 tts/start 探针。

    设备端串口若出现 `STATE: speaking` ⇒ 连接是活的，问题在 TTS 队列；
    若毫无反应 ⇒ 这条连接根本没连到设备（/说给了死连接听）。

    ★ 2026-10-03 扩字段：主人报「一直显示在聆听中、没反应」时，
      光看连接活没活**根本判断不出** —— 那次连接是活的、ASR 也初始化成功了，
      但设备**一个音频包都没发**（唤醒没触发 / 判停太苛刻切碎了句子）。
      现在把「说话链路状态」全暴露出来，这类问题以后扫一眼就知道卡在哪一环：

      链路顺序（正常应该是这样递进）：
        client_have_voice=False → client_voice_start=True → 音频包进来
        → 识别到文本 → LLM 应答 → tts start
      卡在哪一步就一目了然。
    """
    out = {}
    for did, conn in list(REGISTRY.items()):
        ws = getattr(conn, "websocket", None)

        # ── ① 音频/VAD 链路（判断"设备到底有没有在说话"）──
        audio_frames = len(getattr(conn, "asr_audio", []) or [])
        voice = {
            "client_listen_mode": getattr(conn, "client_listen_mode", None),
            "client_have_voice": getattr(conn, "client_have_voice", None),
            "client_voice_start": getattr(conn, "client_voice_start", None),
            "client_voice_stop": getattr(conn, "client_voice_stop", None),
            "asr_audio_frames": audio_frames,      # ★ 0 = 设备一个包都没发
        }
        # ── ② ASR provider 状态（判断"语音识别模块活着吗"）──
        asr = getattr(conn, "asr", None)
        voice["asr_module"] = type(asr).__name__ if asr else None
        asr_ws = getattr(asr, "asr_ws", None) if asr else None
        voice["asr_ws_open"] = bool(getattr(asr_ws, "open", False)) if asr_ws else False
        voice["asr_last_text"] = (getattr(asr, "last_stream_text", "") or "")[-60:]
        # ── ③ LLM / TTS（判断"轮到它说话了吗"）──
        llm = getattr(conn, "llm", None)
        tts = getattr(conn, "tts", None)
        voice["llm_module"] = type(llm).__name__ if llm else None
        voice["tts_module"] = type(tts).__name__ if tts else None
        voice["client_is_speaking"] = getattr(conn, "client_is_speaking", None)
        voice["sentence_id"] = getattr(conn, "sentence_id", None)

        info = {
            "conn_obj": hex(id(conn)),
            "session_id": getattr(conn, "session_id", None),
            "client_abort": getattr(conn, "client_abort", None),
            "ws_state": str(getattr(ws, "state", None)),
            "ws_close_code": getattr(ws, "close_code", None),
            "ws_remote": str(getattr(ws, "remote_address", None)),
            "voice": voice,
        }
        # ── 一句话结论，省得自己推 ──
        if not info["ws_state"] or info["ws_state"] != "1":
            info["verdict"] = "❌ WebSocket 不通，设备连不上"
        elif asr is None:
            info["verdict"] = "❌ ASR 模块没初始化"
        elif voice["asr_audio_frames"] == 0:
            info["verdict"] = ("⚠️ 连接正常但**设备一个音频包都没发** → "
                               "唤醒词没检测到，或设备卡在聆听态。查固件唤醒/VAD。")
        elif voice["client_voice_start"]:
            info["verdict"] = "🔊 正在收音（等用户说完）"
        else:
            info["verdict"] = "✅ 链路正常，设备待机中（等唤醒词）"
        try:
            await ws.send(
                json.dumps(
                    {
                        "type": "tts",
                        "state": "start",
                        "session_id": conn.session_id or "",
                    },
                    ensure_ascii=False,
                )
            )
            info["probe_start"] = "sent"
        except Exception as e:
            info["probe_start"] = f"FAIL {type(e).__name__}: {e}"
        out[did] = info
        logger.bind(tag=TAG).info(f"[诊断] {did} -> {info}")
    return _cors(
        web.json_response({"count": len(REGISTRY), "devices": out})
    )


async def _run_http(config: dict):
    notify_cfg = config.get("notify", {}) or {}
    host = notify_cfg.get("host", "0.0.0.0")
    port = int(notify_cfg.get("port", 8004))

    app = web.Application()
    app.add_routes(
        [
            web.post("/notify", _handle_notify),
            web.get("/notify/status", _handle_status),
            web.get("/notify/diag", _handle_diag),
            web.options("/notify/diag", _handle_options),
            web.post("/remind", _handle_remind_add),
            web.get("/remind/list", _handle_remind_list),
            web.post("/remind/delete", _handle_remind_delete),
            web.options("/notify", _handle_options),
            web.options("/notify/status", _handle_options),
            web.options("/remind", _handle_options),
            web.options("/remind/list", _handle_options),
            web.options("/remind/delete", _handle_options),
        ]
    )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()

    from core.utils.util import get_local_ip

    logger.bind(tag=TAG).info(
        "主动播报/提醒接口\t http://{}:{}/notify  /remind  /notify/status",
        get_local_ip(),
        port,
    )


# ---------------------------------------------------------------- 安装


def install_notify(config: dict) -> bool:
    """在 app.py 里调用一次即可。重复调用安全。"""
    if getattr(ConnectionHandler.handle_connection, "_active_notify", False):
        return True

    _patched_handle_connection._active_notify = True
    ConnectionHandler.handle_connection = _patched_handle_connection

    notify_cfg = dict(config.get("notify", {}) or {})
    announce_enabled = notify_cfg.get("announce_enabled", True)
    # 2026-10-03 新增：任务取件瞬间的"正在处理"回执开关（默认开）
    _ACK_SWITCH[0] = bool(notify_cfg.get("task_ack_enabled", True))

    async def _boot():
        try:
            await _run_http(config)
        except Exception as e:
            logger.bind(tag=TAG).error(f"主动播报接口启动失败: {e}")
        _queue_load()
        _TASKS.append(asyncio.create_task(_drain_queue()))
        if announce_enabled:
            _TASKS.append(asyncio.create_task(_watch_bridge(notify_cfg)))
        _TASKS.append(asyncio.create_task(_reminder_loop(notify_cfg)))
        if notify_cfg.get("exec_enabled", True):
            _TASKS.append(asyncio.create_task(_execute_inbox_loop(notify_cfg)))
        # ★ 未完成事项巡检：主动盯着主人没做完的事并开口提醒
        if notify_cfg.get("todo_watch_enabled", True):
            _TASKS.append(asyncio.create_task(_todo_watch_loop(notify_cfg)))
        # ★ 语音额度守护：快用完时主动提醒 + 2.0 用完自动切 1.0 后告知
        if notify_cfg.get("quota_watch_enabled", True):
            _TASKS.append(asyncio.create_task(_quota_watch_loop(notify_cfg)))
        # ★ 每日凌晨记忆蒸馏：小智侧 + WorkBuddy 侧汇总成一个池子
        if notify_cfg.get("distill_enabled", True):
            _TASKS.append(asyncio.create_task(_distill_daily_loop(notify_cfg)))

    try:
        loop = asyncio.get_running_loop()
        _TASKS.append(loop.create_task(_boot()))
    except RuntimeError:
        logger.bind(tag=TAG).warning(
            "install_notify 不是在事件循环里调用的，主动播报接口未启动"
        )
        return False

    logger.bind(tag=TAG).info(
        f"active-notify 插件已装载（取件播报={announce_enabled}，定时提醒=on，本机执行器=on）"
    )
    return True
