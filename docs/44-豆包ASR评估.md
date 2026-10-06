# 豆包流式 ASR 评估（2026-10-03）

> 主人给了官方文档：双向流式 ASR WebSocket
> https://docs.volcengine.com/docs/DoubaoVoice/bidirectional-streaming-automatic-speech-recognition-websocket
> 要求：①存进知识库 ②看看能不能提高识别准确率 ③声音换成 `zh_female_vv_uranus_bigtts`

---

## 一、声音：已经是它了，无需改

```
selected_module.TTS = HuoshanV3TTS
speaker             = zh_female_vv_uranus_bigtts   ← 当前生效
resource_id         = seed-tts-2.0
```

05:21 那次播报（`93 个包 / 14540 字节`）就是这个音色，已在设备上出声。

---

## 二、豆包流式 ASR 2.0：**你已经开通了**

我按官方文档实测了 4 种组合：

| resource_id | 鉴权头 | 结果 |
|---|---|---|
| **`volc.seedasr.sauc.duration`**（2.0） | **`X-Api-Key`** | ✅ **握手成功** |
| `volc.seedasr.sauc.duration` | X-Api-App-Key + token | 401 |
| `volc.bigasr.sauc.duration`（1.0） | X-Api-Key | 403（1.0 未开通） |
| `volc.bigasr.sauc.duration`（1.0） | X-Api-App-Key + token | 401 |

**结论：2.0 服务已开通，鉴权方式和 TTS 一样只认 appid。**

⚠️ 这和 TTS 是同一类坑：**v3 端点只发 `X-Api-Key`，带 token 反而 401。**
上游 `doubao_stream.py` 默认用的是旧版 `token_auth()`，不改就永远 401。已修：

- `core/providers/asr/doubao_stream.py` 新增 `key_auth()` / `_pick_auth()`
- `auth_method` 默认值从 `token` 改成 `key`

---

## 三、能不能提高识别准确率？—— 能，但要看你要什么

### 官方给的 2.0 能力（对比我们现在的本地 Sherpa）

| 能力 | 豆包 2.0 | 本地 Sherpa | 对我们意味着 |
|---|---|---|---|
| 识别模型 | 大模型 | SenseVoice 小模型 | 口语/长句准确率明显提升 |
| **热词纠错** | ✅ 平台级 + 请求级 | ❌ | ★ **专有名词能听对** |
| 数字规整 ITN | ✅ | ❌ | "三点五"→"3.5"，日期时间尤其有用 |
| 标点预测 | ✅ | ❌ | LLM 收到的文本更规整，少误解 |
| 智能分句 | ✅ | 简易 VAD | 断句更准，LLM 少答偏 |
| 语义顺滑 | ✅ | ❌ | 口语转文字更通顺 |
| 情绪检测 | ✅ | ❌ | 未来可做"听你语气" |
| 说话人分离 | ✅ | ❌ | 分清谁在说话（你一个人用意义不大） |
| 语种检测 | ✅ | 部分 | 中英混说更稳 |

### 我的判断：**最值钱的是热词表**

你现在说"小智""deskemoji""舵机""湾湾小何""WorkBuddy"这类词，本地模型大概率识别成别的东西。
配上热词表，这些词的准确率**几乎必涨**——这是投入产出比最高的一步。

### 但有两个代价

| 代价 | 说明 |
|---|---|
| **隐私** | 音频要出网发到火山。你之前明确偏好"数据不出网" |
| 费用 | 约 1 元/小时。一天聊 1 小时 ≈ 30 元/月 |

**所以我没默认切过去**，做成管理台可一键切换（http://192.168.3.139:8005/ → 引擎 → 耳朵）。

---

## 四、怎么切（随时可换，随时可回）

### 方式 A：管理台（推荐）
打开 http://192.168.3.139:8005/ → **引擎** 组 → **耳朵** 下拉 → 选 `DoubaoASRStream` → 保存 → 重启后台。

### 方式 B：命令行
```bash
# 切到豆包
# 把 data/.config.yaml 的 selected_module.ASR 改成 DoubaoASRStream，然后重启
python tools/start_services.py --stop
python tools/start_services.py --explorer --wait 30
# 想回本地：改回 SherpaASR 再重启
```

已验证 provider 能正常加载：
```
resource_id = volc.seedasr.sauc.duration
auth_method = key
鉴权头 = {'X-Api-Key': 'c105d0ce-...', 'X-Api-Resource-Id': 'volc.seedasr.sauc.duration', ...}
```

---

## 五、上热词表（强烈建议，5 分钟）

火山控制台 → **自学习平台 → 热词管理 → 新建热词表**

一行一个词（**最长 10 字**），可用 `|数字` 设权重 1~10（不写默认 4）。保存后拿到一个 UUID，填到配置里：

```yaml
DoubaoASRStream:
  boosting_table_name: "把那个 UUID 填这里"
```

**建议加的词**（我们项目的专有名词）：
```
小智
deskemoji
舵机
手势
PAJ7620
WorkBuddy
湾湾小何
晓晓
记忆库
待办
主动播报
oanda
```

也可以在发请求时临时带 `corpus` 字段（同一个词对不同场景权重不同），但我们走平台热词表更省事。

---

## 六、已存进知识库

4 条 knowledge 类记忆（`bridge/memory.py`），小智以后被问到相关问题能自己检索出来：

| id | 内容 |
|---|---|
| 8 | 豆包流式 ASR 2.0 端点/鉴权/resource_id + 全部能力清单 |
| 9 | 热词表怎么建、怎么用、对本项目的价值 |
| 10 | 火山语音三件套（ASR / TTS / 音频生成）的区别 |
| 11 | 火山 v3 鉴权要点（TTS 发 x-api-key，ASR 发 X-Api-Key，发错就 401） |

---

## 七、我的建议

1. **今天先这样**：TTS 用火山（已接），ASR 保持本地（隐私优先）。
2. **想试就试**：管理台一键切豆包，实际说几句对比一下。**建议今晚或明天接个麦克风实测**——
   同一个词说三遍，看两个引擎哪个听得准。
3. **热词表值得单独做**：不管最后用哪个 ASR，热词表都是纯收益（Sherpa 不支持热词表，
   那就只在用豆包时有意义）。
4. **隐私敏感场景可以混搭**：比如日常用本地（省心+隐私），需要精确输入专有名词时切豆包。
