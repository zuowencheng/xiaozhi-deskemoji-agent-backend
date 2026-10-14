# 小智桌面智能体 · 后端

> ESP32-S3 桌宠的「大脑」和「手」——跑在你电脑上的**本地语音后台 + 本机执行器**。
> 语音不出网、能力自己说了算、还能主动开口找你。

**🔗 配套固件（设备端）在另一个仓库：** [**xiaozhi-deskemoji-agent**](https://github.com/zuowencheng/xiaozhi-deskemoji-agent)
那里是 ESP32-S3 上跑的东西——表情动画、手势识别、双舵机、语音唤醒。
两个仓库**一软一硬，配套使用**：固件负责"看得见摸得着"，本仓负责"听得懂干得了"。

---

## 一、它到底能干嘛

| 能力 | 说明 | 落在哪 |
|---|---|---|
| **语音对话** | 说话 → 识别 → 大模型 → 语音回你 | `server/xiaozhi-server/` |
| **本机执行** | 让它在电脑上开程序、找文件、查资料、调度任务 | MCP 工具（`bridge/`） |
| **主动播报** | 不用你说话，它自己找你说（提醒、投递任务结果） | 8004 端口 |
| **管理台** | 网页上看状态、发指令 | 8005 端口 |
| **服务自愈** | 挂了自动拉起，开机自动启动 | `tools/svc_watchdog.py` |

---

## 二、架构总览

```
┌────────────────────────┐
│  ESP32-S3 桌宠（固件）  │   ← 另一个仓 xiaozhi-deskemoji-agent
│  麦克风 / 喇叭 / 表情屏  │
│  双舵机 / 手势传感器     │
└───────────┬────────────┘
            │  WebSocket  ws://<本机IP>:8000/xiaozhi/v1/
            ▼
┌──────────────────────────────────────────────┐
│            本仓（跑在你的电脑上）              │
│                                              │
│  :8000  xiaozhi-server  语音服务（VAD/ASR/LLM/TTS）
│  :8003  OTA + 视觉分析                        │
│  :8004  主动播报通道                           │
│  :8005  管理台（admin）                        │
│                                              │
│  MCP 三件套（自研，stdio 协议）                │
│   ├─ local_mcp.py   文件/搜索/网页/命令        │
│   ├─ pc_mcp.py      电脑控制（窗口/程序/音乐）  │
│   └─ life_mcp.py    生活（待办/提醒/天气/新闻） │
│                                              │
│  tools/svc_watchdog.py  看门狗：保活 + 自愈     │
└──────────────────────────────────────────────┘
```

设计文档见 [`docs/`](docs/)：总架构 `19-总体架构确认.md`、后台方案 `06-自建后台方案.md`、
MCP 复用 `85-自研MCP架构与复用指南.md`。

---

## 三、快速开始

### 前置

- Windows 10/11
- Python 3.11（脚本会引导，无需手动装）
- 可选：FFmpeg（音频转码，脚本会带）

### 三步跑起来

```bash
# 1. 装依赖（建 venv + 装精简依赖，不装 2GB 的 torch）
双击 deploy\一键部署.cmd      # 或者：server\install.cmd

# 2. 配 Key（详见 docs/配置指南.md）
copy server\config.example.yaml server\xiaozhi-server\data\.config.yaml
#   然后编辑，填入你的 LLM / ASR Key（最少只要一个 LLM Key）

# 3. 启动
双击 server\start_server.cmd
```

启动成功的标志：

```
Websocket地址是   ws://192.168.x.x:8000/xiaozhi/v1/
OTA接口是         http://192.168.x.x:8003/xiaozhi/ota/
```

体检（任何时候怀疑它没跑好，先跑这个）：

```bash
python tools\doctor.py
```

### 让设备连过来

设备固件里写死了连接地址，**需要重编固件**改成 `ws://<本机IP>:8000/xiaozhi/v1/`。
方法与排期见 `docs/06-自建后台方案.md`。

---

## 四、目录结构

```
.
├── server/              语音服务（上游 xiaozhi-esp32-server 镜像 + 我们的适配）
│   ├── config.example.yaml   ★ 配置模板（复制到 data/.config.yaml 再用）
│   └── xiaozhi-server/       服务端主体
├── bridge/              自研 MCP 工具 + 记忆/待办/配额
├── tools/               部署、体检、看门狗、刷机、串口等运维脚本
├── deploy/              一键部署编排
├── docs/                设计文档（含《配置指南》）
├── firmware/            设备固件（独立 git 仓，补丁在 patches/）
├── hardware/            硬件资料与设备备份
└── runtime/             运行时（npm 包装等，不进仓库）
```

---

## 五、配置与模型

**要准备哪些 Key、去哪申请、本地模型去哪下** —— 全部整理在
👉 [`docs/配置指南.md`](docs/配置指南.md)

要点：
- 本项目**不附带任何真实 Key**，你需要自己申请（最少只要一个 LLM Key）。
- 本项目**不附带大模型权重**，想要离线运行请按配置指南里的链接自行下载。
- 所有密钥填在 `server/xiaozhi-server/data/.config.yaml`，该目录已被 `.gitignore` 排除，**不会进仓库**。

---

## 六、相关仓库

| 仓库 | 内容 |
|---|---|
| [**xiaozhi-deskemoji-agent**](https://github.com/zuowencheng/xiaozhi-deskemoji-agent) | 🔧 **设备固件**（ESP32-S3）：表情、手势、舵机、语音唤醒 |
| **xiaozhi-deskemoji-agent-backend**（本仓） | 🧠 **电脑后台**：语音服务、MCP 工具、主动播报、管理台 |

两个仓配合使用：**固件烧到设备，后台跑在电脑**，同一局域网内通过 WebSocket 通信。

---

## 七、License

MIT —— 见 [LICENSE](LICENSE)。上游 `xiaozhi-server` 部分遵循其原始许可。
