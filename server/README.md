# 自建小智后台（阶段二）

把设备从 xiaozhi.me 云拉回本机：**语音不出网、能力自己说了算、能主动开口播报**。

## 目录

```
server/
├── xiaozhi-server/        上游源码镜像（xinnan-tech/xiaozhi-esp32-server @ main）
├── requirements-base.txt  精简依赖（砍掉 torch / funasr 等 2GB+ 本地大模型件）
├── config.example.yaml    ★ 本机私有配置模板
├── install.cmd            建 venv + 装依赖
└── start_server.cmd       启动服务（自动带 ffmpeg）
```

## 三步跑起来

**1. 装依赖**

双击 `install.cmd`。
用的是免安装 Python 3.11.16（落在 `C:\Users\zuowe\.workbuddy\binaries\python\`，不污染系统、不需要管理员）。

**2. 写配置**

把 `config.example.yaml` 复制成：

```
server/xiaozhi-server/data/.config.yaml
```

（上游会优先读 `data/.config.yaml`，缺失项回落到 `config.yaml`。`data/` 已被 gitignore，密钥不进仓库。）

至少要改的：

| 项 | 说明 |
|---|---|
| `selected_module.ASR` | `SherpaASR` 走本地（离线）/ `DoubaoASR` 走云端（需 Key） |
| `ASR.DoubaoASR.appid` / `access_token` | 火山引擎申请，走云端 ASR 时必填 |
| `selected_module.LLM` | `OllamaLLM` 用本机 qwen3:4b（离线，默认）/`DoubaoLLM` 云端 |
| `mcp_endpoint` | 继续用现有本机桥就保留；自建回执做完后可删 |

**3. 启动**

双击 `start_server.cmd`。看到这几行就成了：

```
Websocket地址是   ws://192.168.x.x:8000/xiaozhi/v1/
OTA接口是         http://192.168.x.x:8003/xiaozhi/ota/
```

窗口别关，Ctrl+C 退出。

## 让设备连过来

设备固件里写死了 xiaozhi.me 的地址，**必须重编固件改成 `ws://<本机IP>:8000/xiaozhi/v1/`**。
方法与排期见 `docs/06-自建后台方案.md` 第八节。

## 运行时位置（都不在系统目录）

```
Python 3.11.16  C:\Users\zuowe\.workbuddy\binaries\python\versions\3.11.16\python\python.exe
venv            C:\Users\zuowe\.workbuddy\binaries\python\envs\xiaozhi-server\
ffmpeg          C:\Users\zuowe\.workbuddy\binaries\ffmpeg\ffmpeg-master-latest-win64-gpl\bin\
日志            server\xiaozhi-server\tmp\server.log
```

## 上游升级

不 fork（fork 会继承公开仓库的可见性约束）。升级方式：重新拉 tarball 覆盖 `xiaozhi-server/`，
然后手动合入我们的改动。我们的自研改动集中在：

- `xiaozhi-server/data/.config.yaml`（私有配置，不进仓库）
- 即将新增的主动播报接口（见文档第九节）
