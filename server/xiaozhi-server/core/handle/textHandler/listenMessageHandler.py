import time
import uuid
import asyncio
from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

from core.utils.dialogue import Message
from core.providers.asr.dto.dto import InterfaceType
from core.handle.receiveAudioHandle import startToChat
from core.handle.reportHandle import enqueue_asr_report
from core.handle.sendAudioHandle import send_stt_message, send_tts_message
from core.handle.textMessageHandler import TextMessageHandler
from core.handle.textMessageType import TextMessageType
from core.utils.util import remove_punctuation_and_length
from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType


TAG = __name__

# ══════════════════════════════════════════════════════════════
# 唤醒问候短语池（2026-10-03 23:15）
# ══════════════════════════════════════════════════════════════
# 主人要求：「每次的回复应该是不一样的，需要结合时间」。
# 为什么是预置短语而不是大模型：
#   走 LLM 有三个硬伤 —— ① 延迟 2~3 秒（主人原话"我说完它才反应"就是这个）
#   ② LLM 不知道你接下来要说什么，可能反问"怎么了"，更烦
#   ③ 语音打断在这块板上做不到（AFE 报 0 个回放通道，无 AEC 参考信号）
#   预置短语延迟 <200ms，既有变化又不牺牲速度。
#
# 三条铁律（别改）：
#   1. 必须是**短句**，1~5 字最佳，播完立刻进聆听
#   2. 绝不能是「我听到了什么」这类**反问**——反问会让人以为它没听清
#   3. 绝不能暗示"你说吧"这种等待指令的正式感，会显得生硬
#
# 想自己加词：在 data/.config.yaml 写
#   wakeup_greetings:
#     - "在"
#     - "来啦"
# 不写就用下面这份默认池。
_DEFAULT_GREETINGS = {
    # 深夜 23:00~05:00
    "night": ["还没睡？", "在呢", "这么晚"],
    # 清晨 05:00~09:00
    "morning": ["早啊", "早", "来了"],
    # 白天 09:00~18:00
    "day": ["在", "嗯", "来了", "听见了"],
    # 傍晚 18:00~23:00
    "evening": ["在", "来了", "嗯", "在呢"],
}


def _pick_greeting(conn) -> str:
    """按时段从短语池随机挑一句。"""
    import random
    from datetime import datetime

    pool = conn.config.get("wakeup_greetings")
    if not isinstance(pool, dict) or not pool:
        pool = _DEFAULT_GREETINGS

    h = datetime.now().hour
    if h >= 23 or h < 5:
        key = "night"
    elif h < 9:
        key = "morning"
    elif h < 18:
        key = "day"
    else:
        key = "evening"

    words = [w for w in (pool.get(key) or []) if w and w.strip()]
    if not words:
        # 配置写坏了也别哑掉，至少要能应一声
        return conn.config.get("wakeup_greeting_text") or "在呢"
    return random.choice(words).strip()



class ListenTextMessageHandler(TextMessageHandler):
    """Listen消息处理器"""

    @property
    def message_type(self) -> TextMessageType:
        return TextMessageType.LISTEN

    async def handle(self, conn: "ConnectionHandler", msg_json: Dict[str, Any]) -> None:
        if "mode" in msg_json:
            conn.client_listen_mode = msg_json["mode"]
            conn.logger.bind(tag=TAG).debug(
                f"客户端拾音模式：{conn.client_listen_mode}"
            )
        if msg_json["state"] == "start":
            # 设备从播放模式切回录音模式,清除所有音频状态和缓冲区
            conn.reset_audio_states()
        elif msg_json["state"] == "stop":
            # 收到stop但asr未初始化，跳过处理
            if conn.asr is None:
                return

            conn.client_voice_stop = True
            if conn.asr.interface_type == InterfaceType.STREAM:
                # 流式模式下，发送结束请求
                asyncio.create_task(conn.asr._send_stop_request())
            else:
                # 非流式模式：直接触发ASR识别
                if len(conn.asr_audio) > 0:
                    asr_audio_task = conn.asr_audio.copy()
                    conn.reset_audio_states()

                    if len(asr_audio_task) > 0:
                        await conn.asr.handle_voice_stop(conn, asr_audio_task)
        elif msg_json["state"] == "detect":
            conn.client_have_voice = False
            conn.reset_audio_states()
            if "text" in msg_json:
                conn.last_activity_time = time.time() * 1000
                original_text = msg_json["text"]  # 保留原始文本
                filtered_len, filtered_text = remove_punctuation_and_length(
                    original_text
                )

                # 检查是否是设备呼叫指令 [device_call]
                if original_text.startswith("[device_call]"):
                    # 提取 tag 后的文本
                    call_text = original_text[len("[device_call]"):].strip()
                    conn.logger.bind(tag=TAG).info(f"收到设备呼叫指令: {call_text}")

                    # 标记为来电接听模式
                    conn.incoming_call = True

                    # 准备开始新会话
                    conn.sentence_id = uuid.uuid4().hex

                    await send_stt_message(conn, call_text)

                    # 等待tts初始化，最多等待3秒
                    start_time = time.time()
                    while time.time() - start_time < 3:
                        if conn.tts:
                            break
                        await asyncio.sleep(0.1)

                    if conn.tts:
                        conn.tts.store_tts_text(conn.sentence_id, call_text)
                        conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id, sentence_type=SentenceType.FIRST, content_type=ContentType.ACTION))
                        conn.tts.tts_one_sentence(conn, ContentType.TEXT, content_detail=call_text)
                        conn.tts.tts_text_queue.put(TTSMessageDTO(sentence_id=conn.sentence_id, sentence_type=SentenceType.LAST, content_type=ContentType.ACTION))

                    # 添加到对话历史，让模型理解上下文
                    conn.dialogue.put(Message(role="assistant", content=call_text))
                    return

                # 识别是否是唤醒词
                is_wakeup_words = filtered_text in conn.config.get("wakeup_words")
                # 是否开启唤醒词回复
                enable_greeting = conn.config.get("enable_greeting", True)

                # ★★ 2026-10-03 23:15 修「唤醒问候连播两遍」—— 上一版窗口不够
                #
                # 实锤（23:00 那次，8 秒窗口**没拦住**）：
                #   23:00:09 detect "你好小智"     → 播「在呢，说吧」
                #   23:00:12 listen start          → 主人开口
                #   23:00:27 识别文本: 你好，小智。  → 距上次播问候 18 秒，超窗口 → 又播一遍
                #   23:00:34 识别文本: 退出。
                # 说明"主人想问题想得久一点"就必然破窗，**按时间猜不可靠**。
                #
                # 正确判据是**会话状态**而不是时钟：唤醒之后这一轮对话里，
                # 唤醒词只应该被认可一次。主人的语音里再冒出唤醒词，一律当误识别。
                # 只有真正回到待机（退下/超时/设备重连）才允许下一次唤醒。
                #
                # 落地：用 session_id 做闸。设备每次「真正唤醒」会带新 session_id
                #   （23:00 那次 detect 是 ...d116，上一次是 ...067b），
                #   同一个 session 内第二次出现的唤醒词 = 误识别。
                #   比时间窗稳，因为不依赖"主人想多久"。
                # 这里的 detect 分支只是**兜底**：真正的主入口在
                #   core/providers/asr/base.py（所有语音输入都从那儿进 startToChat），
                #   闸已放在那里。逻辑统一走 greeting_guard，别在这里重写一遍。
                # 证据：23:24 日志里「识别文本: 你好，小智。」**没有对应的 listen 消息**，
                #   说明主人说的话不走 detect 分支。详见 core/handle/greeting_guard.py。
                from core.handle.greeting_guard import is_dup_wakeup_misrecognition
                if is_wakeup_words and is_dup_wakeup_misrecognition(conn, filtered_text):
                    conn.logger.bind(tag=TAG).info(
                        f"唤醒词去重（detect 分支兜底）：{original_text[:20]}"
                    )
                    is_wakeup_words = False
                    # 标记为主人本轮的真输入，允许下面的 else 分支把它送 LLM
                    # （若内容恰好就是唤醒词本身，则在 else 里单独丢弃）

                if is_wakeup_words and not enable_greeting:
                    # 如果是唤醒词，且关闭了唤醒词回复，就不用回答
                    await send_stt_message(conn, original_text)
                    await send_tts_message(conn, "stop", None)
                    conn.client_is_speaking = False
                elif is_wakeup_words:
                    # ★★ 2026-10-03 22:25 修「唤醒后重复说两遍」
                    # 现象：唤醒 → 说"在呢，说吧" → 主人开始讲话 → 讲完后它**又说一遍**
                    #      "在呢"，然后才正常。
                    # 根因（服务端日志实锤，`大模型收到用户消息: 在呢，说吧。`）：
                    #   原来这里调 `startToChat(conn, greeting)`，而 startToChat 是
                    #   **给用户输入用的入口**，它会 `send_stt_message()` +
                    #   `executor.submit(conn.chat, text)` —— 等于把问候语当成
                    #   "主人刚说了这句话" 喂给 LLM，模型于是回答"在"。
                    #   主人那边的真实语音随后进来，就变成"第二轮重复问候"。
                    #
                    # 修法：**照抄上游 [device_call] 分支的写法（本文件 86-94 行）** ——
                    #   问候语是"设备自己主动说的一句话"，不属于用户输入，
                    #   所以只走 TTS 合成下发，**绝不经过 LLM、绝不当成 user 消息**。
                    #   这与 helloHandle.checkWakeupWords 播预录音频是同一个思路。
                    # ★★ 2026-10-03 23:15 改为**按时段随机短语**（主人要求"每次不一样"）
                    # 为什么不走大模型：唤醒后走 LLM 有三个硬伤 ——
                    #   1) 延迟 2~3 秒（主人原话"我说完它才反应"就是这个）
                    #   2) LLM 不知道你接下来要说什么，可能反问"怎么了"，更烦
                    #   3) 语音打断在这块板上做不到（AFE 0 个回放通道，无 AEC 参考），
                    #      它说话时你插不进去，只能靠唤醒词
                    # 所以：预置短语池按时段随机挑一句，延迟 <200ms，
                    #   既有变化又不牺牲速度。播完绝不让大模型再回一句。
                    greeting = _pick_greeting(conn)
                    conn.just_woken_up = True
                    # ① 等 TTS 就绪（最多 3 秒），否则音频发不出去
                    start_time = time.time()
                    while time.time() - start_time < 3:
                        if conn.tts:
                            break
                        await asyncio.sleep(0.1)

                    if not conn.tts:
                        conn.logger.bind(tag=TAG).warn(
                            "唤醒问候跳过：TTS 未就绪")
                        return

                    # ② 新会话 id，让这句话走独立的 FIRST/MIDDLE/LAST 分段
                    conn.sentence_id = uuid.uuid4().hex
                    conn.client_abort = False
                    conn.client_is_speaking = True

                    # ③ 直播：不 send_stt_message、不进 LLM
                    conn.tts.store_tts_text(conn.sentence_id, greeting)
                    conn.tts.tts_text_queue.put(TTSMessageDTO(
                        sentence_id=conn.sentence_id,
                        sentence_type=SentenceType.FIRST,
                        content_type=ContentType.ACTION,
                    ))
                    conn.tts.tts_one_sentence(conn, ContentType.TEXT,
                                              content_detail=greeting)
                    conn.tts.tts_text_queue.put(TTSMessageDTO(
                        sentence_id=conn.sentence_id,
                        sentence_type=SentenceType.LAST,
                        content_type=ContentType.ACTION,
                    ))

                    # ④ 只作为 assistant 侧上下文留痕，让模型知道"我已经打过招呼了"，
                    #    主人接下来说的话不会被当成又一句唤醒
                    conn.dialogue.put(Message(role="assistant", content=greeting))
                    # 记下时间戳 + 本次 session_id，供上面的唤醒词去重闸使用。
                    # 为什么要 session_id：设备真正唤醒时会带新 session，
                    # 同一个 session 内再冒出唤醒词 = ASR 从主人语音里误识别 → 判为误识别。
                    # 只记时间戳不够 —— 23:00 实锤：主人想问题想了 18 秒，
                    # 超过任何合理时间窗，问候语照样连播。
                    conn.greeting_played_at = time.time()
                    conn.greeting_session_id = msg_json.get("session_id")
                    conn.logger.bind(tag=TAG).info(
                        f"已播唤醒问候: {greeting}（直播，不经 LLM，"
                        f"session={conn.greeting_session_id}）")
                else:
                    conn.just_woken_up = True
                    # ★ 2026-10-03 23:15：去重闸命中时，如果内容**就是唤醒词本身**，
                    #   说明主人开口说的其实是"你好"这类被打断/误识别的音，不是真在提问。
                    #   丢掉它，否则 LLM 会去回答"你好小智"是什么。
                    #   ⛔ 一旦内容里**除了唤醒词还有别的话**（"你好帮我查油价"），
                    #      照常送 LLM —— 绝不丢主人的真问题。
                    #   判据用 session（与上面去重闸同源），不再用时间窗。
                    _ww = conn.config.get("wakeup_words") or []
                    _is_dup_misrecognition = (
                        is_wakeup_words is False
                        and filtered_text in _ww
                        and getattr(conn, "greeting_session_id", None)
                        == msg_json.get("session_id")
                    )
                    if _is_dup_misrecognition:
                        conn.logger.bind(tag=TAG).info(
                            f"丢弃同会话内重复的纯唤醒词：{original_text[:20]}"
                            f"（这轮已播过问候，判定为 ASR 误识别）")
                        await send_stt_message(conn, original_text)
                        await send_tts_message(conn, "stop", None)
                        conn.client_is_speaking = False
                        return
                    # 上报纯文字数据（复用ASR上报功能，但不提供音频数据）
                    enqueue_asr_report(conn, original_text, [])
                    # 否则需要LLM对文字内容进行答复
                    await startToChat(conn, original_text)