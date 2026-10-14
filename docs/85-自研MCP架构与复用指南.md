# 85 · 自研 MCP 架构与复用指南

> 2026-10-14。主人说：play_music 插件摘掉；自研 MCP 这套**先梳理出架构，我可能要给其他产品复用**；梳理完再决定要不要单独抽包推官方。
> 本文是这套 MCP 的**唯一权威结构说明**，复用/迁移照这份走。

---

## 一、一句话定位

我们用 **3 个纯 Python、stdio 协议的自研 MCP Server**，把小智（语音入口）的指令接到「这台 Windows 电脑 + 一份本地记忆/待办」上。
**零 npm、零联网依赖、离线可用、可审计**——这是相对官方/第三方 MCP 的核心差异点。

---

## 二、全景架构（当前活跃链路）

```
┌─────────────────┐  WebSocket   ┌──────────────────────────────────────┐
│ ESP32-S3 设备    │ ───────────► │ xiaozhi-server (app.py)  :8000       │
│ (小智 deskemoji) │              │  ① ASR→LLM(DeepSeek)→TTS              │
└─────────────────┘              │  ② 连接建立时读 MCP 配置，按需起子进程 │
                                 └───────────────┬──────────────────────┘
                                                 │ stdio (JSON-RPC over stdin/stdout)
                        ┌────────────────────────┼────────────────────────┐
                        ▼                        ▼                        ▼
              ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
              │ xiaozhi-local    │   │ xiaozhi-pc       │   │ xiaozhi-life     │
              │ (8~9 tools)      │   │ (14 tools)       │   │ (11 tools)       │
              │ 纯 stdlib        │   │ 依赖 pywin32     │   │ 依赖 pywin32+httpx│
              └────────┬─────────┘   └────────┬─────────┘   └────────┬─────────┘
                       │ 无复用             │ import 复用            │ import 复用
                       │                    ▼                        ▼
                       │        ┌───────────────────────┐  ┌──────────────────────────┐
                       │        │ bridge/server_agent.py │  │ bridge/server_agent.py    │
                       │        │  (M1 系统状态/M4 派单)  │  │  (记忆 remember/recall…)  │
                       │        │ core/pc_agent.py       │  │ bridge/todo.py (待办SQLite)│
                       │        │  (M2 桌面/M3 文件)      │  │ core/pc_music.py (音乐)    │
                       │        │                        │  │ core/pc_agent.py (搜索)    │
                       │        └───────────────────────┘  └──────────────────────────┘
                       ▼
              runtime/logs/*.log（日志只写文件，绝不污染 stdout）
              bridge/memory/memory.db（SQLite：记忆 + 待办，同一个库）
              bridge/{inbox,done,announced}/（任务投递箱协议）
```

**挂载配置**：`server/xiaozhi-server/data/.mcp_server_settings.json`
**服务端读配置时机**：**只在连接建立时读一次** → 改配置**必须重启服务端**才生效（铁律 16）。
**每 Server 初始化超时**：10s（慢的会被丢弃，工具就没了）。

> 兼容说明：`bridge/mcp_pipe.py` + `bridge/server_agent.py` 是**早期接小智官方云 wss 接入点**的那条路（哑管道 wss↔stdio）。
> 自建后台后已不走它，但 `server_agent.py` 被当**实现库**复用（pc/life 两个 Server 都 import 它）。`mcp_pipe.py` 保留备用。

---

## 三、三层结构

| 层 | 文件 | 职责 | 能否直接搬到别的产品 |
|---|---|---|---|
| **① 挂载层** | `data/.mcp_server_settings.json` | 声明 command/args，服务端据此起子进程 | 要改：venv 路径 + 脚本路径（含本机绝对路径） |
| **② MCP 包装层** | `bridge/tools/{local,pc,life}_mcp.py` | 用 `FastMCP` 注册工具，做参数校验、try/except、口语化返回 | **通用**，写得很薄，只依赖标准库 + mcp |
| **③ 实现层** | `server_agent.py` / `pc_agent.py` / `pc_music.py` / `memory.py` / `todo.py` | 真正干活的函数 | 大部分通用，少数绑本机路径/品牌 |

**关键设计取舍：包装层不重写实现。** ②③ 分离的好处是「行为零漂移」——同一个实现既给 MCP 用，也能被服务端内部直接调；换 MCP 框架时只动①。

---

## 四、工具清单总表（编号沿用能力清单 9~35）

### xiaozhi-local（本机文件/网络/命令）——纯 stdlib，最轻
| 工具 | 作用 | 通用性 |
|---|---|---|
| `list_dir` / `read_file` / `write_file` | 目录/读写文件（白名单内） | ★★★（`ALLOWED_ROOTS` 需参数化） |
| `search_files` / `grep_files` | 按名 / 按内容搜索 | ★★★ |
| `fetch_url` | 抓网页正文（httpx + bs4） | ★★★ **推荐**（补官方已下架的 npm fetch） |
| `run_shell` | 跑一条命令（黑名单拦截） | ★★ 敏感，`CMD_BLACKLIST` 需可配 |
| `now` | 当前时间 | ★★★ **推荐**（补官方已下架的 npm time） |
| `everything_search` | Everything 秒搜（**装了 es.exe 才注册**） | ★ Windows 专属 |

### xiaozhi-pc（Windows 桌面控制）——依赖 pywin32
| 工具 | 作用 | 通用性 |
|---|---|---|
| `get_pc_status` / `list_running_apps` | CPU/内存/磁盘/进程 | ★★★ |
| `open_app` | 启动白名单程序 | ★★（`APP_WHITELIST` 含本机路径，需参数化） |
| `list_windows` / `window_action` / `send_keys` / `screenshot` / `clipboard` | 窗口/按键/截屏/剪贴板 | ★★★ |
| `open_path` | 双击式打开 | ★★★ |
| `organize_dir` | 整理目录（**默认 apply=False 只出计划**） | ★★ |
| `dispatch_agent` / `query_task` / `list_recent_tasks` / `get_pending_results` | 任务投递箱 | ★ **项目专属**，不宜推官方 |

### xiaozhi-life（生活助理）——记忆/待办/音乐/搜索
| 工具 | 作用 | 通用性 |
|---|---|---|
| `remember` / `recall` / `list_memories` / `forget` | 长期记忆（SQLite+FTS5+向量） | ★★★ **推荐** |
| `todo_add` / `todo_list` / `todo_done` / `todo_del` | 待办 | ★★★ **推荐** |
| `search_web` | 必应 HTML 免 key 搜索 | ★★★ |
| `music_open` / `music_control` | 唤起播放器 + 系统媒体键 | ★ 绑"酷狗"，可泛化成"默认播放器" |

---

## 五、关键设计纪律（迁移时**必须照抄**，都是踩过的坑）

1. **stdout 就是 JSON-RPC 协议流**：任何 `print` 都会让握手失败。所有诊断信息写 `runtime/logs/<name>_mcp.log`。
2. **docstring 必须是普通字符串**，写成 f-string 会让 FastMCP 发出的 `description` 变空串 → 模型乱选工具（bridge/README 排障表第 3 条）。
3. **每个工具包 try/except**：单工具炸了不能拖垮整个 Server。
4. **懒加载 + 优雅降级**：`_init_impls()` 里逐个 import，失败记 `_import_err` 并返回友好文案，不让 Server 起不来。
5. **分组挂载**：拆成 3 个 Server（不是 1 个 33 工具的巨无霸）。工具太多会降低模型选工具准确率；拆开还能用 `tools/mcp_toggle.py` 单独关一组。
6. **安全边界写死在实现层**：`ALLOWED_ROOTS`（防路径穿越）、`CMD_BLACKLIST`、`APP_WHITELIST`、`organize_dir` 默认 dry-run。
7. **日志编码**：子进程中文 stderr 是 cp936，一律 `errors="replace"`，否则解码异常会断链。

---

## 六、依赖与运行时

- **Python**：`C:\Users\zuowe\.workbuddy\binaries\python\envs\xiaozhi-server`（3.11.16，服务端同一个 venv）
- **包装层必需**：`mcp[cli]<2`（实测 1.22.0）
- **实现层按需**：
  - `psutil`（server_agent 状态/进程）
  - `pywin32`（pc_agent 窗口/按键/剪贴板、pc_music 唤起）
  - `httpx` + `beautifulsoup4`（fetch_url）
  - `websockets` + `python-dotenv`（仅 mcp_pipe 旧路径）
  - 记忆的**向量**用本地 Ollama（`bge-m3` 优先 / `nomic-embed-text` 兜底），**没有也不影响**——退回纯 SQLite 全文检索
- **`bridge/requirements.txt`**（对外模板）：`mcp[cli]<2 / websockets>=13 / python-dotenv / psutil`

---

## 七、数据落点

| 内容 | 位置 |
|---|---|
| 记忆 + 待办（同一库） | `bridge/memory/memory.db`（SQLite + FTS5 虚表） |
| 任务投递箱 | `bridge/inbox/`（待办）→ `bridge/done/`（完成）→ `bridge/announced/`（已播报标记） |
| MCP 日志 | `runtime/logs/{local,pc,life}_mcp.log` |
| 挂载配置 | `server/xiaozhi-server/data/.mcp_server_settings.json` |

---

## 八、复用指南（搬到别的产品，照这 5 步）

**目标形态**：把这套抽成两个**独立、无个人依赖**的 MCP Server 包：

- `xiaozhi-mcp-local` ← 文件 + 时间 + 抓网页（**纯 stdlib + httpx/bs4**，零 Windows 依赖，跨平台）
- `xiaozhi-mcp-life` ← 记忆 + 待办 + 搜索（**SQLite + 必应**）

**步骤**：
1. **抄②包装层**：`local_mcp.py` / `life_mcp.py` 整体拿走，只改 `FastMCP("名字")`。
2. **把写死的东西改成入参/环境变量**：
   - `ALLOWED_ROOTS` → 启动参数或 `XIAOZHI_MCP_EXTRA_DIRS`（已支持 env 追加，见 local_mcp.py:41）
   - `CMD_BLACKLIST` → 配置文件
   - 记忆 DB 路径 → 参数
3. **实现层按需替换**：`pc_mcp` 依赖 pywin32，**只在 Windows 上有意义**；跨平台产品要么砍掉，要么换开源 Windows Control MCP 顶替（只换 `pc_mcp.py` 一个文件）。
4. **分发**：以独立 repo / PyPI 走，README 给 `pip install` + 配置片段（JSON 挂载示例直接抄现在这份）。
5. **自测**：每个 Server 都带 `--selftest`（发 `initialize`+`tools/list`+只读实调用），判据 = **工具齐全 + description 非空**。发布前必跑。

**差异化卖点（对官方）**：官方 Python 服务端 `xinnan-tech/xiaozhi-esp32-server` 虽然支持 MCP，但 `local` / `life` 这两组工具它是缺的，且我们的 **fetch_url / now 正好补上官方已下架的 npm fetch/time**；再加「离线可用 + Windows 免 npm」。

---

## 九、推官方前必须脱敏的字段

| # | 字段 | 出现在 |
|---|---|---|
| 1 | 绝对路径（`C:\Users\zuowe\...`） | `.mcp_server_settings.json`、`tools/start_services.py:29`、`deploy/setup.py` |
| 2 | API Key（DeepSeek / 火山 appid+token / 和风天气） | `data/.config.yaml` |
| 3 | 设备标识 MAC `84:c7:bb:58:17:44` | `.config.yaml` `allowed_devices` |
| 4 | 局域网 IP `192.168.3.137` / `.139` | 各处配置/文档 |
| 5 | `local_mcp.ALLOWED_ROOTS`（`Path.home()/Desktop` …） | `local_mcp.py:36` |
| 6 | `server_agent.APP_WHITELIST`（含本机 chrome/edge 路径） | `server_agent.py:51` |
| 7 | 品牌名「酷狗」 | `pc_music.py`、`life_mcp.music_open` docstring |

---

## 十、已知边界 / 坑

- **服务端初始化只给 10s**：本机 npm 被安全策略拦（reg.exe 黑名单）→ 第三方 npm MCP 根本起不来 → 这才是我们自研的根本原因，不是"想自研"。
- **配置只在连接时读一次**：改挂载配置后必须重启服务端（重启会断设备 WS 5~15s，见铁律 25）。
- **工具同名冲突**：挂载 `xiaozhi-life` 时必须同步摘掉插件版的 `memory_tools` / `todo_tools` / `pc_music_control`，否则记忆工具同名，模型分不清。
- **play_music 插件**：2026-10-14 已从 `data/.config.yaml` 的 functions 列表摘除（与 life_mcp 的 `music_open/music_control` 功能重叠）；配套改了 `agent-base-prompt.txt` 里的强制调用指引（play_music → music_open）。
