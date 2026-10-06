# 34 · 主动播报静默的**真**根因：`ContentType` 传了枚举类而不是枚举成员

> 2026-10-03 02:28　四轮误判之后终于钉死。一句话：**服务端日志骗了我们整整三轮。**

---

## 1. 现象

服务端每次播报都打印：

```
[播报] 已投递 FULL(FIRST/TEXT/LAST) sid=xxxx
发送第一段语音: 主人，...
发送音频消息: SentenceType.LAST, 主人，...
```

看起来完美。**但设备一声不吭**，65 秒串口日志里连 `tts/start` 的影子都没有。

---

## 2. 前三次误判（都错了，留档警示）

| 轮次 | 当时的结论 | 真相 |
|---|---|---|
| 第 1 轮 | 提醒 `last_fired` 有值 → 以为通了 | 后台"以为自己发了"，设备没收到 |
| 第 2 轮 | "没发结束信号 / 线程死了" | 是必要条件，但不是根因 |
| 第 3 轮 | "AnimationTask 栈溢出把设备打重启了" | 这个是**真实存在的 bug**（已修，栈 4096→10240），但修完仍无声 |

**教训**：栈溢出那个 bug 是真的，它让"播报→重启"的现象极具迷惑性，掩盖了真正的发送缺陷。两个 bug 叠在一起，各修一个都看不到效果。

---

## 3. 决定性手段：给 `sendAudioMessage` 挂字节数探针

光看"发送第一段语音"这种日志没用——它在 `sendAudioMessage` **函数开头**就打印，跟到底有没有音频数据完全无关。

在 `core/notify_plugin.py` 里 monkey-patch `core.providers.tts.base.sendAudioMessage`，记录 `audios` 的真实包数/字节数：

```python
def _wrapped(conn, sentence_type, audios, text, sentence_id=None):
    n  = len(audios) if isinstance(audios, (list, tuple, bytes, bytearray)) else 0
    nb = sum(len(x) for x in audios) if isinstance(audios, (list, tuple)) else len(audios)
    ...  # LAST 时汇总输出一行
```

**修复前**：
```
[音频探针] type=LAST 包数=0 字节=0 msg_sid=6fa822f3 conn_sid=6fa822f3
```
全程只有 1 个包，0 字节 → `sendAudio(conn, [])` 啥也不发。

**修复后**：
```
[音频探针] 本次播报合计 34 个包 / 2280 字节
```

**这一条日志是唯一能分清"没合成"和"发了没到"的手段**，以后排查同类问题先挂它。

---

## 4. 根因：枚举类 ≠ 枚举成员

`core/providers/tts/base.py` 的 `tts_text_priority_thread` 里：

```python
if message.sentence_type == SentenceType.FIRST:
    ...                                        # 只重置状态，不碰文本
elif ContentType.TEXT == message.content_type:
    self.tts_text_buff.append(message.content_detail)   # 唯一填充缓冲区的地方
    segment_text = self._get_segment_text()
    if segment_text:
        self.to_tts_stream(segment_text, opus_handler=self.handle_opus)
if message.sentence_type == SentenceType.LAST:
    self._process_remaining_text_stream(opus_handler=self.handle_opus)  # 冲出剩余
    self.tts_audio_queue.put((LAST, [], text, sentence_id))
```

而我原来的 `speak()` 里是这样取 content_type 的：

```python
content_type = getattr(_connection_mod, "ContentType", None)   # ← 这是枚举【类】
if content_type is None:
    from core.providers.tts.dto.dto import ContentType as content_type
```

`ContentType`（类）从来不等于 `ContentType.TEXT`（成员），所以：

1. MIDDLE 消息**没有**被 `append` 进 `tts_text_buff`
2. LAST 时 `_process_remaining_text_stream()` 面对的是**空缓冲区**，合成 **0 字节**
3. `sendAudioMessage(conn, LAST, [], text, sid)` → `sendAudio(conn, [])` → 一个包都不发
4. 但函数开头的 `发送第一段语音` / `发送音频消息: LAST` **照常打印**

→ **服务端日志完全正常，设备永远静音。**

---

## 5. 修复

按 `core/connection.py` 上游的规范投递（这也避免了另一个坑）：

```python
from core.providers.tts.dto.dto import ContentType, SentenceType, TTSMessageDTO

sid = uuid.uuid4().hex
target.sentence_id = sid            # 必须同步写回 conn，否则被 sentence_id 过滤
q = tts.tts_text_queue
q.put(TTSMessageDTO(sid, SentenceType.FIRST,  ContentType.ACTION, None))  # 只重置
q.put(TTSMessageDTO(sid, SentenceType.MIDDLE, ContentType.TEXT,   text))  # 唯一填充点
q.put(TTSMessageDTO(sid, SentenceType.LAST,   ContentType.ACTION, None))  # 冲出剩余
```

**为什么 FIRST / LAST 用 `ACTION` 而不是 `TEXT`**：
上游 `connection.py` 就是这么写的。如果 LAST 也传 `TEXT`，它会先走 `elif ContentType.TEXT == ...` 分支再 `append` 一遍文本，
然后 `_process_remaining_text_stream()` 又合成一次 → **同一句话播两遍**。

### 其余三个必要条件（缺一即静默，且日志同样看不出来）

1. `tts_one_sentence` 只塞文本、**从不发 LAST** → 短句凑不够分段就永远憋在缓冲区，必须自己补 LAST
2. `if message.sentence_id != self.conn.sentence_id: continue` → `sentence_id` 必须写回 `conn.sentence_id`
3. 设备 idle 久了两个消化线程会退出 → 播报前检查 `tts_priority_thread` / `audio_play_priority_thread`.`is_alive()`，死了就 `tts.open_audio_channels(conn)` 重新拉起
4. `if self.conn.client_abort: continue` → 播报前 `conn.client_abort = False`，否则上次打断过后续全被跳过

---

## 6. 修复后验证

服务端：
```
[音频探针] 本次播报合计 34 个包 / 2280 字节
```

设备端串口（四轮以来第一次出现 `<<`）：
```
I (270320) Application: << 主人，主动播报修好了
I (273420) Application: << 如果你听到这句话，就说明我真的能主动找你说话了
```

`<<` 是设备端收到 TTS 文本并进入 speaking 的标志。同时设备**不再崩溃**（栈已加大）。

---

## 7. 顺带确认的好消息

同一份日志里出现了：
```
EmojiController: 执行向左看动画
EmojiController: 舵机向左转动
EmojiController: 舵机向右转动
```
→ 10-03 早些时候那次固件修复（开机建表情屏 + 补 `CheckSequentialGestures()` 调用）**确实生效了**，舵机动了。

---

## 8. 排查同类问题的固定套路（写进项目规矩）

1. **别信"发送成功"类日志** —— 它们往往在函数开头打印，跟数据内容无关
2. **先挂字节数探针** —— 分清"没合成"还是"发了没到"
3. **同步抓设备串口** —— `Application: <<` 是设备真的收到 TTS 的铁证
4. **栈溢出会让现象彻底变形** —— 设备重启会伪装成"掉线重连"，先把崩溃修干净再查别的
