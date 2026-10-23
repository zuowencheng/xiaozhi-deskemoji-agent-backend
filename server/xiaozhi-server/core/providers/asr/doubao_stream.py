import json
import gzip
import uuid
import asyncio
import websockets
from core.providers.asr.base import ASRProviderBase
from config.logger import setup_logging
from core.providers.asr.dto.dto import InterfaceType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


class ASRProvider(ASRProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__()
        self.interface_type = InterfaceType.STREAM
        self.config = config
        self.text = ""
        self.last_stream_text = ""   # 流式最新文本（旁路，见 _forward_asr_results）
        self.asr_ws = None
        self.forward_task = None
        self.is_processing = False  # 添加处理状态标志
        self._is_stopping = False  # 添加停止标志，防止竞态条件

        # 配置参数
        self.appid = str(config.get("appid"))
        self.access_token = config.get("access_token")
        # 资源ID，用于区分不同的ASR模型（默认1.0模型小时版，v2版本使用seed-asr）
        self.resource_id = config.get("resource_id", "volc.bigasr.sauc.duration")

        self.boosting_table_name = config.get("boosting_table_name", "")
        self.correct_table_name = config.get("correct_table_name", "")
        self.output_dir = config.get("output_dir", "tmp/")
        self.delete_audio_file = delete_audio_file

        # 火山引擎ASR配置
        enable_multilingual = config.get("enable_multilingual", False)
        self.enable_multilingual = (
            False if str(enable_multilingual).lower() == "false" else True
        )
        if self.enable_multilingual:
            self.ws_url = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream"
        else:
            self.ws_url = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
        self.uid = config.get("uid", "streaming_asr_service")
        self.workflow = config.get(
            "workflow", "audio_in,resample,partition,vad,fe,decode,itn,nlu_punctuate"
        )
        self.result_type = config.get("result_type", "single")
        self.format = config.get("format", "pcm")
        self.codec = config.get("codec", "pcm")
        self.rate = config.get("sample_rate", 16000)
        # language参数仅在多语种模式(bigmodel_nostream)下有效
        self.language = config.get("language") if self.enable_multilingual else None
        self.bits = config.get("bits", 16)
        self.channel = config.get("channel", 1)
        self.auth_method = config.get("auth_method", "key")
        self.secret = config.get("secret", "access_secret")
        end_window_size = config.get("end_window_size")
        self.end_window_size = int(end_window_size) if end_window_size else 200

    async def open_audio_channels(self, conn):
        await super().open_audio_channels(conn)

    async def _ensure_connected(self, conn: "ConnectionHandler"):
        """★ 2026-10-14 02:38 主人反馈：唤醒后立即说话仍无反应。

        根因：原 receive_audio 只在 `have_voice=True` 时才建 ASR WebSocket。
          主人唤醒后停顿 1~2 秒想问题，这期间 AFE 不产 PCM / 发静音帧，
          服务端 `audio_have_voice` 一直是 False，ASR 永远不建连；
          等到豆包 8s 超时断开 → 主人开口时又走 6s 重新建连 → 第一句掉光。

        修法：listen start 一进来就主动建连。仍走 receive_audio 同一段建连代码，
          复用 `is_processing` 防并发。失败就退回原来"有声音再建"路径。
        """
        if self.asr_ws is not None or self.is_processing:
            return
        # 用一个伪 PCM 帧走 super + 自家建连的链路。
        # 60ms 静音帧（16kHz/16bit/单声道 = 1920B），不会触发 VAD，但能进入
        # receive_audio → 撞 audio_have_voice=False 那条不建连 —— 不行。
        # 所以这里直接复刻 receive_audio 的建连段，不走 super。
        try:
            self.is_processing = True
            headers = self._pick_auth()
            logger.bind(tag=TAG).info(
                f"[主动建连] listen start 触发 ASR 提前建连: {headers}")
            self.asr_ws = await websockets.connect(
                self.ws_url,
                additional_headers=headers,
                max_size=1000000000,
                ping_interval=None,
                ping_timeout=None,
                close_timeout=10,
            )
            request_params = self.construct_request(str(uuid.uuid4()))
            try:
                # ⚠️ 2026-10-14 修：原写法用了未定义常量 SERVER_FULL_REQUEST 且漏了 gzip，
                #   必然抛 NameError（日志 "name 'SERVER_FULL_REQUEST' is not defined"），
                #   → "提前建连"其实一直没生效（一直靠 receive_audio 的兜底路径）。
                #   现改为与 receive_audio 完全同款：无参 generate_header()
                #   （默认 message_type=0x01 FullClientRequest, compression=gzip）+ gzip.compress。
                payload_bytes = str.encode(json.dumps(request_params))
                payload_bytes = gzip.compress(payload_bytes)
                full_client_request = self.generate_header()
                full_client_request.extend((len(payload_bytes)).to_bytes(4, "big"))
                full_client_request.extend(payload_bytes)
                logger.bind(tag=TAG).info(
                    f"[主动建连] 发送初始化请求: {request_params}")
                await self.asr_ws.send(full_client_request)
                init_res = await self.asr_ws.recv()
                result = self.parse_response(init_res)
                logger.bind(tag=TAG).info(
                    f"[主动建连] 收到初始化响应: {result}")
                if "code" in result and result["code"] != 1000:
                    raise Exception(
                        "[主动建连] ASR初始化失败: "
                        + str(result.get("payload_msg", {}).get("error", "未知错误")))
            except Exception as _e:
                logger.bind(tag=TAG).error(f"[主动建连] 初始化失败: {_e}")
                if self.asr_ws:
                    await self.asr_ws.close()
                    self.asr_ws = None
            # 启动结果接收任务
            self.forward_task = asyncio.create_task(self._forward_asr_results(conn))
        except Exception as _e:
            logger.bind(tag=TAG).error(f"[主动建连] 失败: {_e}")
        finally:
            # ★ 2026-10-19 修（实锤）：成功建连后**必须保持 is_processing=True**。
            #   发帧条件是 receive_audio 里那句
            #       `if self.asr_ws and self.is_processing and not self._is_stopping:`
            #   这里无条件置 False → 连接建好了却一帧都不发 → 豆包约 10s 判
            #   "Timed out waiting for the next audio packet" 把连接踢掉 →
            #   主人在这个空窗里说的话全丢，只能等重连后再说一遍。
            #
            #   症状：每次它播报完（"已开启手势识别"/"有意思"…）回到待听，
            #   紧接着的 9~10 秒说什么都没反应，显得"反应迟钝、喊不动"。
            #   日志铁证（2026-10-19）：
            #     21:08:35 收到listen start → 主动建连成功
            #     21:08:44 Timed out waiting for the next audio packet  ← 9s 静默
            #     21:08:45 重连 → 21:08:52 才认出"退下吧"
            #   全日志 6 天共 96 次，其中 10-14 一天 44 次。
            #
            #   只在**没连上**时回退标志位（connect 抛异常 / 初始化失败会把
            #   asr_ws 关掉并置 None），与 receive_audio 正常路径保持一致。
            if self.asr_ws is None:
                self.is_processing = False

    async def receive_audio(self, conn: "ConnectionHandler", pcm_frame, audio_have_voice):
        # 先调用父类方法处理基础逻辑
        await super().receive_audio(conn, pcm_frame, audio_have_voice)

        # 如果本次有声音，且之前没有建立连接
        if audio_have_voice and self.asr_ws is None and not self.is_processing:
            try:
                self.is_processing = True
                # 建立新的WebSocket连接
                headers = self._pick_auth()
                logger.bind(tag=TAG).info(f"正在连接ASR服务，headers: {headers}")

                self.asr_ws = await websockets.connect(
                    self.ws_url,
                    additional_headers=headers,
                    max_size=1000000000,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=10,
                )

                # 发送初始化请求
                request_params = self.construct_request(str(uuid.uuid4()))
                try:
                    payload_bytes = str.encode(json.dumps(request_params))
                    payload_bytes = gzip.compress(payload_bytes)
                    full_client_request = self.generate_header()
                    full_client_request.extend((len(payload_bytes)).to_bytes(4, "big"))
                    full_client_request.extend(payload_bytes)

                    logger.bind(tag=TAG).info(f"发送初始化请求: {request_params}")
                    await self.asr_ws.send(full_client_request)

                    # 等待初始化响应
                    init_res = await self.asr_ws.recv()
                    result = self.parse_response(init_res)
                    logger.bind(tag=TAG).info(f"收到初始化响应: {result}")

                    # 检查初始化响应
                    if "code" in result and result["code"] != 1000:
                        error_msg = f"ASR服务初始化失败: {result.get('payload_msg', {}).get('error', '未知错误')}"
                        logger.bind(tag=TAG).error(error_msg)
                        raise Exception(error_msg)

                except Exception as e:
                    logger.bind(tag=TAG).error(f"发送初始化请求失败: {str(e)}")
                    if hasattr(e, "__cause__") and e.__cause__:
                        logger.bind(tag=TAG).error(f"错误原因: {str(e.__cause__)}")
                    raise e

                # 启动接收ASR结果的异步任务
                self.forward_task = asyncio.create_task(self._forward_asr_results(conn))

                # 发送缓存的音频数据
                if conn.asr_audio and len(conn.asr_audio) > 0:
                    for cached_pcm in conn.asr_audio[-10:]:
                        try:
                            payload = gzip.compress(cached_pcm)
                            audio_request = bytearray(
                                self.generate_audio_default_header()
                            )
                            audio_request.extend(len(payload).to_bytes(4, "big"))
                            audio_request.extend(payload)
                            await self.asr_ws.send(audio_request)
                        except Exception as e:
                            logger.bind(tag=TAG).info(
                                f"发送缓存音频数据时发生错误: {e}"
                            )

            except Exception as e:
                logger.bind(tag=TAG).error(f"建立ASR连接失败: {str(e)}")
                if hasattr(e, "__cause__") and e.__cause__:
                    logger.bind(tag=TAG).error(f"错误原因: {str(e.__cause__)}")
                if self.asr_ws:
                    await self.asr_ws.close()
                    self.asr_ws = None
                self.is_processing = False
                return

        # 发送当前音频数据
        if self.asr_ws and self.is_processing and not self._is_stopping:
            try:
                payload = gzip.compress(pcm_frame)
                audio_request = bytearray(self.generate_audio_default_header())
                audio_request.extend(len(payload).to_bytes(4, "big"))
                audio_request.extend(payload)
                await self.asr_ws.send(audio_request)
            except Exception as e:
                logger.bind(tag=TAG).info(f"发送音频数据时发生错误: {e}")

    async def _collect_remainder(self, conn, first_text: str, window: float = 1.2) -> str:
        """拿到 definite 之后，再收一小段把被切掉的后半句补回来。

        为什么必须有这段（2026-10-03 23:40 实锤，详见 docs/57）
        -----------------------------------------------------
        豆包 ASR 会在一句话**说到一半**时就给 definite=True（语义断句），
        上游的逻辑是「一收到 definite 立刻 handle_voice_stop + break」。
        那次主人的话是「还没呢你」，日志里 ASR 其实给全了：

            duration 1980  definite=True   text='你好，小智。'      ← 上游在这里就收工了
            duration 2580                  text='你好，小智。还没'
            duration 2940  prefetch=True   text='你好，小智。还没呢你'  ← 真正的内容，永远没机会提交

        结果：前半截被当成"主人只说了唤醒词"丢掉，后半截随连接关闭蒸发，
        主人干等 18 秒只听到一句莫名其妙的「拜拜」。

        做法：definite 之后最多再等 window 秒，取这段时间里**累积全文**最长的一条。
        ASR 的 result.text 是累积式的（越往后越全），所以取最长那条就是完整句。
        结束条件：① 新帧带 last_stream_duration（ASR 明确说这段结束了）
                  ② 窗口超时 ③ 连接/会话被停 ④ 出错（一律退回已识别文本，绝不抛）
        """
        import asyncio
        import time as _time

        best = first_text
        try:
            deadline = _time.monotonic() + window
            while self.asr_ws and not conn.stop_event.is_set():
                left = deadline - _time.monotonic()
                if left <= 0:
                    break
                try:
                    response = await asyncio.wait_for(self.asr_ws.recv(), timeout=left)
                except asyncio.TimeoutError:
                    break

                result = self.parse_response(response)
                payload = result.get("payload_msg", {}) or {}
                pres = payload.get("result", {}) or {}
                # 累积全文：'你好，小智' → '你好，小智。还没' → '你好，小智。还没呢你'
                full = pres.get("text") or ""
                if full and len(full) > len(best):
                    best = full
                    try:
                        self.last_stream_text = full
                    except Exception:
                        pass
                # ASR 明确标记整段结束 → 不用再等
                if pres.get("last_stream_duration"):
                    break
        except Exception as e:
            # 续接收集绝不能影响主流程：出错就用已经识别到的文本
            logger.bind(tag=TAG).debug(f"续接收集异常，按已识别文本提交: {e}")

        if best != first_text:
            logger.bind(tag=TAG).info(f"[续接] 补齐后半句: {first_text} → {best}")
        return best

    async def _forward_asr_results(self, conn: "ConnectionHandler"):
        try:
            while self.asr_ws and not conn.stop_event.is_set():
                # 获取当前连接的音频数据
                audio_data = conn.asr_audio
                try:
                    response = await self.asr_ws.recv()
                    result = self.parse_response(response)
                    # ★ 用 info 而不是 debug：识别结果默认看不到，排查"没出文本"时完全瞎
                    logger.bind(tag=TAG).info(f"收到ASR结果: {str(result)[:300]}")
                    # ★ 旁路：把流式最新文本另存一份。
                    #    show_utterances=True 时 provider 只在 definite=True 才写 self.text，
                    #    调试/对比脚本取不到中间结果（旧日志里只能靠人眼从 info 行里扒）。
                    try:
                        _t = (result.get("payload_msg", {}).get("result", {}) or {}).get("text")
                        if _t:
                            self.last_stream_text = _t
                    except Exception:
                        pass

                    if "payload_msg" in result:
                        payload = result["payload_msg"]
                        # 检查是否是错误码1013（无有效语音）
                        if "code" in payload and payload["code"] == 1013:
                            # 静默处理，不记录错误日志
                            continue

                        if "result" in payload:
                            utterances = payload["result"].get("utterances", [])
                            # 检查duration和空文本的情况
                            if (
                                not self.enable_multilingual  # 注意：多语种模式不返回中间结果，需要等待最终结果
                                and payload.get("audio_info", {}).get("duration", 0)
                                > 2000
                                and not utterances
                                and not payload["result"].get("text")
                                and conn.client_listen_mode != "manual"
                            ):
                                logger.bind(tag=TAG).error(f"识别文本：空")
                                self.text = ""
                                if len(audio_data) > 15:  # 确保有足够音频数据
                                    await self.handle_voice_stop(conn, audio_data)
                                break

                            # 专门处理没有文本的识别结果（手动模式下可能已经识别完成但是没松按键）
                            elif not payload["result"].get("text") and not utterances:
                                # 多语种模式会持续返回空文本，直到最后返回完整结果，所以需要排除
                                if self.enable_multilingual:
                                    continue

                                if conn.client_listen_mode == "manual" and conn.client_voice_stop and len(audio_data) > 15:
                                    logger.bind(tag=TAG).debug("消息结束收到停止信号，触发处理")
                                    await self.handle_voice_stop(conn, audio_data)
                                    break

                            for utterance in utterances:
                                if utterance.get("definite", False):
                                    current_text = utterance["text"]
                                    logger.bind(tag=TAG).info(
                                        f"识别到文本: {current_text}"
                                    )

                                    # 手动模式下累积识别结果
                                    if conn.client_listen_mode == "manual":
                                        if self.text:
                                            self.text += current_text
                                        else:
                                            self.text = current_text

                                        # 在接收消息中途时收到停止信号
                                        if conn.client_voice_stop and len(audio_data) > 0:
                                            logger.bind(tag=TAG).debug("消息中途收到停止信号，触发处理")
                                            await self.handle_voice_stop(conn, audio_data)
                                        break
                                    else:
                                        # 自动模式：见 _collect_remainder 的踩坑说明，
                                        # 拿到 definite 不能直接收工，否则后半句会被截掉。
                                        self.text = await self._collect_remainder(
                                            conn, current_text
                                        )
                                        audio_data = conn.asr_audio
                                        if len(audio_data) > 15:  # 确保有足够音频数据
                                            await self.handle_voice_stop(
                                                conn, audio_data
                                            )
                                    break
                        elif "error" in payload:
                            error_msg = payload.get("error", "未知错误")
                            logger.bind(tag=TAG).error(f"ASR服务返回错误: {error_msg}")
                            break

                except websockets.ConnectionClosed:
                    logger.bind(tag=TAG).info("ASR服务连接已关闭")
                    self.is_processing = False
                    break
                except Exception as e:
                    logger.bind(tag=TAG).error(f"处理ASR结果时发生错误: {str(e)}")
                    if hasattr(e, "__cause__") and e.__cause__:
                        logger.bind(tag=TAG).error(f"错误原因: {str(e.__cause__)}")
                    self.is_processing = False
                    break

        except Exception as e:
            logger.bind(tag=TAG).error(f"ASR结果转发任务发生错误: {str(e)}")
            if hasattr(e, "__cause__") and e.__cause__:
                logger.bind(tag=TAG).error(f"错误原因: {str(e.__cause__)}")
        finally:
            if self.asr_ws:
                await self.asr_ws.close()
                self.asr_ws = None
            self.is_processing = False
            self._is_stopping = False
            # 重置所有音频相关状态
            conn.reset_audio_states()

    def stop_ws_connection(self):
        if self.asr_ws:
            asyncio.create_task(self.asr_ws.close())
            self.asr_ws = None
        self.is_processing = False
        self._is_stopping = False

    async def _send_stop_request(self):
        """发送最后一个音频帧以通知服务器结束"""
        self._is_stopping = True  # 先标记为停止状态，阻止后续音频发送
        if self.asr_ws:
            try:
                # 发送结束标记的音频帧（gzip压缩的空数据）
                empty_payload = gzip.compress(b"")
                last_audio_request = bytearray(
                    self.generate_last_audio_default_header()
                )
                last_audio_request.extend(len(empty_payload).to_bytes(4, "big"))
                last_audio_request.extend(empty_payload)
                await self.asr_ws.send(last_audio_request)
                logger.bind(tag=TAG).debug("已发送结束音频帧")
            except Exception as e:
                logger.bind(tag=TAG).debug(f"发送结束音频帧时出错: {e}")

    def construct_request(self, reqid):
        req = {
            "app": {
                "appid": self.appid,
                "token": self.access_token,
            },
            "user": {"uid": self.uid},
            "request": {
                "reqid": reqid,
                "workflow": self.workflow,
                "show_utterances": True,
                "result_type": self.result_type,
                "sequence": 1,
                "end_window_size": self.end_window_size,
                "corpus": {
                    "boosting_table_name": self.boosting_table_name,
                    "correct_table_name": self.correct_table_name,
                }
            },
            "audio": {
                "format": self.format,
                "codec": self.codec,
                "rate": self.rate,
                "bits": self.bits,
                "channel": self.channel,
                "sample_rate": self.rate,
            },
        }

        # language参数仅在多语种模式下添加
        if self.enable_multilingual and self.language:
            req["audio"]["language"] = self.language

        logger.bind(tag=TAG).debug(
            f"构造请求参数: {json.dumps(req, ensure_ascii=False)}"
        )
        return req

    def token_auth(self):
        """旧版鉴权（v1 老控制台）：X-Api-App-Key + X-Api-Access-Key
        ⚠️ 实测对 v3 端点（bigmodel_async）会 **HTTP 401**，别用。"""
        return {
            "X-Api-App-Key": self.appid,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

    def key_auth(self):
        """★ 新版 v3 鉴权（2026-10-03 实测可用）：只发 X-Api-Key，不发 token。

        火山 v3 端点（wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async）
        握手时只认 appid；带 X-Api-Access-Key 反而 401。
        实测：seedasr + X-Api-Key → 握手成功；bigasr + X-Api-Key → 403（1.0 未开通）。
        """
        return {
            "X-Api-Key": self.appid,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }

    def _pick_auth(self):
        """按配置选鉴权方式。auth_method: key=新版(默认) / token=旧版"""
        return self.key_auth() if self.auth_method == "key" else self.token_auth()

    def generate_header(
        self,
        version=0x01,
        message_type=0x01,
        message_type_specific_flags=0x00,
        serial_method=0x01,
        compression_type=0x01,
        reserved_data=0x00,
        extension_header: bytes = b"",
    ):
        header = bytearray()
        header_size = int(len(extension_header) / 4) + 1
        header.append((version << 4) | header_size)
        header.append((message_type << 4) | message_type_specific_flags)
        header.append((serial_method << 4) | compression_type)
        header.append(reserved_data)
        header.extend(extension_header)
        return header

    def generate_audio_default_header(self):
        return self.generate_header(
            version=0x01,
            message_type=0x02,
            message_type_specific_flags=0x00,
            serial_method=0x01,
            compression_type=0x01,
        )

    def generate_last_audio_default_header(self):
        return self.generate_header(
            version=0x01,
            message_type=0x02,
            message_type_specific_flags=0x02,
            serial_method=0x01,
            compression_type=0x01,
        )

    def parse_response(self, res: bytes) -> dict:
        try:
            # 检查响应长度
            if len(res) < 4:
                logger.bind(tag=TAG).error(f"响应数据长度不足: {len(res)}")
                return {"error": "响应数据长度不足"}

            # 获取消息头
            header = res[:4]
            message_type = header[1] >> 4

            # 如果是错误响应
            if message_type == 0x0F:  # SERVER_ERROR_RESPONSE
                code = int.from_bytes(res[4:8], "big", signed=False)
                msg_length = int.from_bytes(res[8:12], "big", signed=False)
                error_msg = json.loads(res[12:].decode("utf-8"))
                return {
                    "code": code,
                    "msg_length": msg_length,
                    "payload_msg": error_msg,
                }

            # 获取JSON数据
            try:
                # 检查字节8-11是否为有效的JSON长度字段
                # 格式：4字节头 + 4字节序列号 + 4字节长度 + JSON数据
                length = int.from_bytes(res[8:12], "big")
                if length > 0 and length <= len(res) - 12:
                    # 有长度字段，从字节12开始读取指定长度的JSON
                    json_data = res[12:12 + length].decode("utf-8")
                else:
                    # 无长度字段或长度无效，尝试直接解析
                    json_data = res[8:].decode("utf-8")
                result = json.loads(json_data)
                logger.bind(tag=TAG).debug(f"成功解析JSON响应: {result}")
                return {"payload_msg": result}
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                logger.bind(tag=TAG).error(f"JSON解析失败: {str(e)}")
                logger.bind(tag=TAG).error(f"原始数据: {res}")
                raise

        except Exception as e:
            logger.bind(tag=TAG).error(f"解析响应失败: {str(e)}")
            logger.bind(tag=TAG).error(f"原始响应数据: {res.hex()}")
            raise

    async def speech_to_text(self, opus_data, session_id, artifacts=None):
        result = self.text
        self.text = ""  # 清空text
        return result, None

    async def close(self):
        """资源清理方法"""
        if self.asr_ws:
            await self.asr_ws.close()
            self.asr_ws = None
        if self.forward_task:
            self.forward_task.cancel()
            try:
                await self.forward_task
            except asyncio.CancelledError:
                pass
            self.forward_task = None
        self.is_processing = False
