# 35 · 主动播报「一句都听不到」的最终真根因：没人发 tts/start

> 2026-10-03 02:40 定位并修复。**已实测出声，用户口头确认"可以听到"。**
> 前面 32 / 33 / 34 三篇都只是"让服务端看起来正常"，这一篇才是最后那道锁。

## 1. 现象（极具迷惑性）

| 环节 | 看起来 | 实际 |
|---|---|---|
| 服务端日志 | `语音生成成功` / `发送第一段语音` / `发送音频消息: FIRST/LAST` 一应俱全 | ✅ 真的发了 |
| 设备串口 | `Application: << 提醒你` | ✅ 收到了（但只是**文本**） |
| 设备屏幕 | 有字幕 | ✅ 显示了（同上） |
| **喇叭** | — | ❌ **一声不吭** |

也就是说：**文字到了，声音没到。** 前面几轮就是被"设备串口都打印了"骗过去的。

## 2. 根因：固件只在 speaking 状态才解码音频包

`firmware/community/main/application.cc:421`

```cpp
protocol_->OnIncomingAudio([this](std::unique_ptr<AudioStreamPacket> packet) {
    if (device_state_ == kDeviceStateSpeaking) {          // ← 关键判断
        audio_service_.PushPacketToDecodeQueue(std::move(packet));
    }
});
```

而让设备切进 speaking 的那条 `{"type":"tts","state":"start"}`，**原本只在正常对话里由 STT 环节顺手发出**：

`core/handle/sendAudioHandle.py:346`（`send_stt_message` 末尾）
```python
await send_tts_message(conn, "start")   # 用户说完话 → STT → 回 start → 设备切 speaking
```

**主动播报（定时提醒 / 任务完成播报）没有 STT 这一环 → 从来没有谁发过 `tts/start`**
→ 设备一直停在 `idle` → 服务端发来的 opus 包被上面那个 `if` **静默丢弃**。

### 三条消息的分工（别搞混）

| 消息 | 谁发的 | 设备收到后干什么 |
|---|---|---|
| `tts / state=start` | 正常对话：`send_stt_message`；**主动播报：没人发（本次修复补上）** | 切 `speaking` → **音频包才会被解码** |
| `tts / state=sentence_start` | `sendAudioMessage` 的 FIRST 分支 | 只显示文本（串口那句 `<< xxx` 就是它） |
| `tts / state=stop` | `sendAudioMessage` 的 LAST 分支 | 退出 speaking → 转 idle / listening |

## 3. 修复

### 主修复（一定会生效）
`server/xiaozhi-server/core/handle/sendAudioHandle.py` — `sendAudioMessage()` 的 FIRST 分支开头补一条：

```python
if sentenceType == SentenceType.FIRST:
    await send_tts_message(conn, "start")   # ← 新增。此处本就在 conn 的事件循环内，必定送达
    ...原有 sentence_start 逻辑...
```

好处：这条路径覆盖**所有**播报（正常对话重复发一次也无害，固件已在 speaking 时会忽略）。

### 辅助（尽力而为，失败无所谓）
`core/notify_plugin.py` 新增 `_send_tts_state(conn, state)`，在投递 TTS 队列**之前**先预热一次 start，
让设备早一点进 speaking。

⚠️ 实测这条走 `asyncio.run_coroutine_threadsafe(..., conn.loop)` 会 **3 秒超时**（`TimeoutError` 的
`str(e)` 是空字符串，日志里表现为"失败: "后面啥都没有——这个空消息坑了我一轮）。
所以只能当"提前量"，**不能当唯一的指望**，真正兜底必须放在 `sendAudioHandle` 里。

## 4. 验证方法（强烈建议以后都这么验）

光看日志**永远验不出**有没有出声，因为这条链路上每一环都会打"成功"。硬证据只有一个：

> **看 LAST 之后设备有没有发 `{"type":"listen","state":"start"}`。**

固件 `stop` 分支是 `if (device_state_ == kDeviceStateSpeaking) { → listening }`。
设备只有在**真的进过 speaking** 的情况下才会在 stop 后转 listening。

| 版本 | LAST 之后 | 结论 |
|---|---|---|
| 修复前（02:30 那条定时提醒） | 无 listen 消息 | 设备没进 speaking，音频全丢 |
| 修复后（02:42 测试） | `02:42:13 收到listen消息 state=start` | 真进了 speaking → 真播了 |

**最终确认**：02:42:16 服务端 ASR 识别到用户说 **「可以听到。」** —— 用户亲耳听到了。

## 5. 副作用（已知，暂不改）

播报结束后设备按 `kListeningModeAutoStop` 从 speaking 转 **listening**，会开麦克风等着接话。
好处是播报完可以直接对话（用户就是这么回的"可以听到"→"退下吧"）；
坏处是环境噪声可能被误识别成一句话。若嫌吵，后续可在 stop 后再补一段把设备拉回 idle 的逻辑。

## 6. 踩坑复盘

1. **"日志说成功" ≠ "用户听到了"**。这条链路上每一步都会打成功日志，唯独最后一环（设备解码）没有任何日志。
2. **跨线程往 `conn.loop` 投协程会超时**，且 `TimeoutError` 的 `str()` 是空的 —— 日志只能打"失败: "，
   看不出是超时还是别的。以后所有 `future.result(timeout=)` 的 except 里必须打 `type(e).__name__` 和 `repr(e)`。
3. 排查设备"没反应"类问题时，**状态机**比"数据有没有发出去"更值得先看。
