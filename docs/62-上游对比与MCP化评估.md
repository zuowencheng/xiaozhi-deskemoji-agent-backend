# 62 · 与上游对比 & 自有能力的 MCP 化评估

> 建档：2026-10-04 · 成哥决定「不再单独养分支，改成基于上游最新版 + MCP 接入自有能力」

---

## 〇、一句话结论

**可行，而且我们早就走过这条路，只是后来绕开了。**

`bridge/server_agent.py` 就是一个**现成的 MCP Server**（FastMCP，12 个工具），
`bridge/mcp_pipe.py` 是官方生态标准的 wss↔stdio 管道——阶段一接官方云 xiaozhi.me 时用的就是它。

但有三条硬边界必须先认清，否则路线会走歪：

| # | 边界 | 说明 |
|---|---|---|
| 1 | **MCP 只能搬「LLM 主动调用」的能力** | 搬不动「服务端主动说话」（主动播报/到点提醒）和「会话管道」（唤醒去重/ASR 续接/免打扰） |
| 2 | **固件永远搬不走** | MCP 协议够不到固件层（任务栈、UI 状态机、板级外设） |
| 3 | 但主动播报**可以做成上游插件** | 上游插件函数签名是 `async def f(conn: "ConnectionHandler", ...)`，**拿得到 conn** → 能主动发 TTS，不必改核心 |

> 换句话说：**"完全不 fork"做不到，但"把 fork 面压缩到极小（一个板级目录 + 1~2 处 hook）"做得到。**

---

## 一、上游现状（三个必须先知道的事实）

| # | 事实 | 对我们的意义 |
|---|---|---|
| 1 | **服务端我们已经在用最新版**：xinnan-tech/xiaozhi-esp32-server 最新 **v0.9.7（2026-09-29）**，我们日志里就是 `[0.9.7_...]` | 服务端不存在"版本落后"问题——问题只是我们**改了它的源码**（且服务端目录**没有 git**，改了什么只能靠 docs 回忆） |
| 2 | **固件上游已跳到 ESP-IDF 6.0+**：78/xiaozhi-esp32 要求 **IDF 6.0.1（推荐 6.1）**，**5.x 全面停止支持**；我们跑在 **5.5.4** | ⚠️ 这是升级最大门槛：换上游固件 = 先换 IDF 大版本 |
| 3 | **上游原生支持两类 MCP**：设备端 `device_mcp` + 服务端 `server_mcp`（stdio 直接挂）/ `mcp_endpoint`（走外部注册中心） | **不需要 hack**，官方就是这样设计的 |

补充：上游固件支持 **70+ 硬件平台 + 自定义板级（`docs/custom-board.md`）**——
我们的表情屏板 `esp32-s3n16r8-emoji` 属于**社区 fork（TranBinh2311）自带**的板级，官方树里没有。

---

## 二、我们比上游多了什么

### 2.1 固件层（`git diff --stat`：**21 文件，+787 −161**）

| 文件 | 改动量 | 性质 |
|---|---|---|
| `main/application.cc` / `.h` | +161 / +5 | **聆听超时**（CLOCK_TICK 计时回 idle）+ 栈调整 |
| `main/protocols/protocol.h/.cc`、`websocket_protocol.cc` | +6/+9/+37 | **`listening_timeout_sec` 字段解析** + `on_connected_()` 补调修复 |
| `main/boards/esp32-s3n16r8-emoji/*`（6 文件） | +83 等 | **板级**：表情屏、舵机、手势传感器 |
| `main/display/oled_display.cc` | +25 | 显示相关修复 |
| `main/audio/*`（audio_service、afe_audio_processor） | +61+16+8 | 音频链路 |
| `main/assets/*`、`lang_config.h` | 二进制/多语言 | 提示音、文案 |
| `sdkconfig` / `sdkconfig.defaults` | +335 / +4 | 配置（栈大小等） |

**分类**：
- 🟢 **通用修复/特性**（值得推上游 PR）：聆听超时、`on_connected_` 缺调、栈溢出修复、AFE 相关
- 🟡 **板级支持**（上游没有，只能自己带）：`esp32-s3n16r8-emoji/` 整个目录
- ⚪ **纯配置**：sdkconfig 差异（换 IDF 大版本本来也要重来）

### 2.2 服务端 · **插件层**（新增文件，天然抗升级）✅

位置 `server/xiaozhi-server/plugins_func/functions/`，我们自研的：

| 插件 | 能力 |
|---|---|
| `pc_control.py` | 桌面操控（窗口/按键/剪贴板/截屏等） |
| `pc_music_control.py` | 音乐控制 |
| `todo_tools.py` | 待办 CRUD |
| `reminder.py` | 定时提醒（LLM 侧） |
| `memory_tools.py` | 记忆读写 |
| `llm_switch.py` | 切换大模型 |

> 这一层**本身就是"插件形式"，不是改核心**——上游升级时新文件不会被覆盖。
> 这是我们的**资产**，也是 MCP 化最好的起点。

### 2.3 服务端 · **核心层**（改 `core/`，**升级会被覆盖**）⚠️

| 文件 | 干什么 |
|---|---|
| `core/notify_plugin.py` | **主动播报**、定时提醒调度、待办巡检、inbox 常驻执行器、免打扰、时段闸 |
| `core/pc_agent.py` | 本机执行器（17 个工具 + LLM 循环 + 预算控制） |
| `core/pc_desktop.py` / `pc_music.py` / `task_board.py` / `workdir.py` | 桌面/音乐/看板/工作目录 |
| `core/handle/greeting_guard.py` | 唤醒去重 |
| `core/providers/asr/base.py`（改动） | ASR 续接窗口 `_collect_remainder()` |
| `core/connection.py`（改动） | 下发 `listening_timeout_sec` |
| `server/admin/admin_server.py` | 管理台（**独立进程 8005，零服务端耦合**） |

**这些是升级时的"成本区"**，也是本次要重点瘦身的对象。

### 2.4 `bridge/`（阶段一 MCP 资产，**仍然可用**）

| 文件 | 作用 |
|---|---|
| `mcp_pipe.py` | wss↔stdio 哑管道（官方生态标准做法） |
| `server_agent.py` | **本机 MCP Server（FastMCP），12 个工具** |
| `memory.py` / `todo.py` / `quota.py` / `llm_usage.py` / `distill.py` | 记忆/待办/额度/用量/蒸馏 |
| `selftest_stdio.py` | **不发网络就能自测** `initialize` + `tools/list` |

`server_agent.py` 现有工具：`get_pc_status`、`list_running_apps`、`open_app`、`read_text_file`、
`dispatch_agent`、`query_task`、`list_recent_tasks`、`get_pending_results`、
`remember`、`recall`、`list_memories`、`forget`。

> 它还实现了一个巧思 `_tail()`：**任何工具返回时搭一句"顺便汇报"**——
> 这是云方案下"最接近主动播报"的折中（源码注释里作者自己承认了）。

---

## 三、MCP 化可行性矩阵（按模块）

| # | 模块 | 现在的形态 | MCP 化 | 说明 |
|---|---|---|---|---|
| M1 | 本机状态（CPU/内存/磁盘/进程） | bridge MCP | ✅ **已是 MCP** | 直接复用 `server_agent.py` |
| M2 | 应用与窗口（打开/窗口/按键/截屏/剪贴板） | 插件 `pc_control` + `pc_agent` | ✅ 可搬 | 已有现成开源 MCP（Windows Control/NutJS） |
| M3 | 文件（读/写/列目录/搜索/整理） | `pc_agent` 工具 | ✅ 可搬 | 官方 filesystem MCP 或 `Everything Search` MCP |
| M4 | 任务派发（inbox 投递箱） | `pc_run_task` + `dispatch_agent` | ✅ 可搬 | **两份实现同源**，合并成一个 MCP 工具即可 |
| M5 | 记忆（remember/recall/…） | 插件 `memory_tools` + bridge | ✅ 可搬 | 已有 MCP 版 |
| M6 | 待办 CRUD | 插件 `todo_tools` | ✅ 可搬 | 也可接 Todoist MCP |
| M7 | 音乐控制 | 插件 `pc_music_control` | ✅ 可搬 | 亦可接 Spotify MCP |
| M8 | 搜索/天气/新闻 | 上游插件 | ✅ 已在 | 上游自带 |
| M9 | **主动播报**（服务端推 TTS） | **改核心** `notify_plugin` | ❌ MCP 做不到 → **改做上游插件** | 插件能拿 `conn`，可主动发 TTS |
| M10 | **到点提醒 / 待办巡检** | **改核心** `notify_plugin` | ❌ 同上 | 同上，做成插件的后台线程 |
| M11 | 设备控制（音量/屏幕/舵机/表情） | 固件 `device_mcp` | 保持设备端 MCP | 上游原生机制 |
| M12 | 会话管道（唤醒去重/ASR 续接/免打扰） | **改核心** | ❌ 不是工具，是"会话行为" | 只能留补丁（面积小、hook 点稳定） |
| M13 | 聆听超时（固件） | **改固件** | ❌ 协议够不到 | 推上游 PR 或留 patch |
| M14 | 管理台（8005） | 独立进程 | — | **本来就零耦合**，不用动 |

**统计**：14 个模块里 **8 个可 MCP 化**（且 3 个已经是），
**4 个要靠插件化改造**（M9/M10 主动类、M12 管道类、M13 固件类）。

---

## 四、必须保留的 fork 面 & 对策

| 对象 | 最小保留量 | 对策 |
|---|---|---|
| **固件板级** `esp32-s3n16r8-emoji/` | 整个目录（6 文件） | 按上游 `docs/custom-board.md` 做成**自定义板级**，只贴这一个目录 |
| **固件通用修复**（聆听超时等） | 少量 | **优先推上游 PR**（都是通用缺陷，上游大概率收）；未合并前用 `firmware/patches/` 常驻重放 |
| **服务端主动播报/提醒** | 1 个插件文件 + 1 处加载 hook | 改造成 `plugins_func/functions/` 下的自研插件（**新增文件，抗升级**） |
| **服务端会话管道**（唤醒去重/ASR 续接） | 2~3 处 | 保留小补丁 + 一个"升级后重放"脚本 |
| **管理台** | 0 | 独立进程，完全不受影响 |

> 目标形态：**升级上游 = `git pull` + 重放 patch 集 + 重启**，而不是"手工合并一堆冲突"。

---

## 五、外部可复用的 MCP（扩充能力）

按"与本机 Windows 环境 + 已有能力的适配度"排：

| 优先级 | MCP | 用途 | 备注 |
|---|---|---|---|
| ⭐⭐⭐ | **Windows Control**（NutJS） | 鼠标/键盘/截屏/剪贴板/窗口管理 | **和我们自研的 pc_control 功能重叠**——可直接替换，省维护 |
| ⭐⭐⭐ | **Everything Search** | Windows 文件秒级搜索 | 你本机**已经在用 Everything**，零额外成本 |
| ⭐⭐⭐ | **Windows CLI** | 安全执行 PowerShell/CMD/Git Bash | 比自研 `run_shell` 更可控（白名单） |
| ⭐⭐⭐ | **Filesystem**（官方） | 文件读写 + 目录树 | 标准件，比自研更稳 |
| ⭐⭐ | **Playwright / Browser MCP** | 浏览器自动化、抓网页 | 补"看不了网页"的短板 |
| ⭐⭐ | **Memory**（官方知识图谱） | 跨会话持久记忆 | 但**别和现有 `memory.py` 双写**，二选一 |
| ⭐⭐ | **Tavily / Brave / Exa Search** | LLM 友好搜索 | 补齐搜索后端 |
| ⭐⭐ | **CalDAV / Google Calendar / Outlook** | 日历读写 | 日程提醒的外挂来源 |
| ⭐⭐ | **Email**（支持 QQ/163/126） | 收发邮件 | 国内邮箱可用，适配好 |
| ⭐ | **Todoist / Google Tasks** | 待办 | 和自研 todo 重叠，看要不要换 |
| ⭐ | **Spotify** | 音乐播放控制 | 国内可用性一般 |
| ⭐ | **Pandoc / Excel** | 文档格式转换 | 周报场景有用 |
| ⭐ | **Docker** | 容器管理 | 本机暂无 Docker |

> ⚠️ **不要贪多**。业界经验：**装 3~7 个**就够了，工具列表太长会显著降低模型选工具的准确率。
> 建议首批只上：Filesystem + Windows CLI + Everything Search +（视需要）Playwright。

---

## 六、落地路线（三阶段）

### 阶段一 · 打通通道，零风险（不动现有系统）
1. 决定走哪条：
   - **同机**：上游 `server_mcp` 通道 **stdio 直接挂** `server_agent.py`（**最简单，零额外组件**）
   - **跨机/多设备**：`mcp_endpoint` + 起一个 `mcp-endpoint-server`（**注意端口冲突，见第七节**）
2. 用 `bridge/selftest_stdio.py` 自测工具注册（判据：**工具齐全 + description 非空**）
3. 双跑一段时间：MCP 工具与现有插件并存，观察命中率

### 阶段二 · 把"工具类"能力迁到 MCP
4. 把 `pc_agent.py` 的 17 个工具 + `pc_control`/`todo_tools`/`memory_tools`/`pc_music_control` **合并进 MCP server**
5. 迁完后**从服务端删掉对应插件**，减少升级冲突面
6. 桌面控制优先评估**换成开源 Windows Control MCP**（省自己维护）

### 阶段三 · 处理"搬不走"的部分
7. **主动播报/提醒 → 重写成上游插件**（拿 `conn` 主动发 TTS），从 `notify_plugin` 里搬出来
8. **管道类补丁**：整理成 `patches/` + 一个"上游升级后自动重放"脚本
9. **固件**：板级目录独立管理；通用修复推上游 PR

---

## 七、动手前必须先解决的三个问题

| # | 问题 | 说明 | 建议 |
|---|---|---|---|
| 1 | **8004 端口撞车** | 我们自研的**主动播报**占用 8004；`mcp-endpoint-server` **默认也是 8004** | 二选一改端口：建议**把 mcp-endpoint-server 改到 8006**（我们的 8004 已固化在代码/配置/文档里，动它代价大） |
| 2 | **本机没有 Docker** | `mcp-endpoint-server` 官方推荐 Docker，但**支持源码跑**（`python main.py`） | 同机场景**根本不需要它**，直接 stdio 挂载最省事 |
| 3 | **token / `.env` 安全** | MCP 接入点 token 泄露 = 别人能调你电脑上的工具 | `.env` 已在 `.gitignore`；推 GitHub 前再核一遍（项目里已经有明文 API key 的历史） |

---

## 八、给成哥的一句话

**方向对，但不是"用 MCP 就不 fork 了"，而是"用 MCP 把 fork 面从『一大片』压到『一个板级目录 + 两三处 hook』"。**
而且我们**已经有一半的铺垫**（bridge 的 MCP server、plugins_func 里的自研插件），
**不用从零开始**——这是最大的好消息。
