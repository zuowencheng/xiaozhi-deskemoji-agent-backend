# 63 · MCP 化蓝图与工具映射（可执行清单）

> 配套 `docs/62`。62 回答"能不能做"，本文回答"具体怎么落"。
> 原则：**扩展现有 `bridge/`，不另起炉灶**——它已经是标准 MCP Server 形态。

---

## 一、目标目录结构

```
bridge/                          ← 升级为「本机 MCP Server 包」
├── mcp_pipe.py                  （已有）wss↔stdio 管道，跨机时才用
├── server_agent.py              （已有）入口：聚合各模块 + mcp.run()
├── selftest_stdio.py            （已有）自测：initialize + tools/list
├── tools/                       ← 新增，按模块拆文件
│   ├── __init__.py              统一注册表 register_all(mcp)
│   ├── sysinfo.py               M1 系统状态
│   ├── desktop.py               M2 窗口/按键/截屏/剪贴板
│   ├── files.py                 M3 文件读写/搜索/整理
│   ├── tasks.py                 M4 任务派发与汇报
│   ├── memory.py                M5 记忆
│   ├── todo.py                  M6 待办
│   ├── music.py                 M7 音乐
│   └── shell.py                 M8 命令执行（白名单）
├── memory.py / todo.py / quota.py / llm_usage.py   （已有，被 tools/ 复用）
└── README.md                    （已有，补充新增工具）
```

> 为什么按模块拆文件：① 每个文件独立可测 ② 上游/开源 MCP 可以**整文件替换**（比如 `desktop.py` 换成开源 Windows Control MCP 的实现）

---

## 二、工具映射表（现有两套 → 合并后的 MCP 工具）

**去重前**：`pc_agent.py` 17 个 + `bridge/server_agent.py` 12 个 = 29 个（`read_text_file` 重复）
**合并后**：约 24 个，统一命名与描述风格。

| 模块 | 新 MCP 工具名 | 来源 | 备注 |
|---|---|---|---|
| M1 | `get_pc_status` | bridge | 复用 |
| M1 | `list_running_apps` | bridge ← `list_processes` | 二合一，用 bridge 版 |
| M2 | `open_app` | bridge | 复用（白名单） |
| M2 | `list_windows` | pc_agent | 搬 |
| M2 | `window_action` | pc_agent | 搬 |
| M2 | `send_keys` | pc_agent | 搬 |
| M2 | `screenshot` | pc_agent | 搬 |
| M2 | `clipboard` | pc_agent | 搬（读写二合一） |
| M3 | `read_text_file` | 两边都有 | **合并一份** |
| M3 | `write_text_file` | pc_agent | 搬（**加路径白名单**） |
| M3 | `list_dir` | pc_agent | 搬 |
| M3 | `search_files` | pc_agent | 搬；建议换 **Everything Search MCP** |
| M3 | `organize_dir` | pc_agent | 搬（**高风险，加 dry-run**） |
| M3 | `open_path` | pc_agent | 搬 |
| M4 | `dispatch_agent` | bridge ← +`pc_run_task` | **两份实现同源，合并成一个** |
| M4 | `query_task` | bridge | 复用 |
| M4 | `list_recent_tasks` | bridge | 复用 |
| M4 | `get_pending_results` | bridge | 复用（搭车汇报机制） |
| M5 | `remember` / `recall` / `list_memories` / `forget` | bridge | 复用，4 个 |
| M6 | 待办 CRUD | `todo_tools.py`（插件） | 搬进来，与 `todo.py` 同源 |
| M7 | 音乐控制 | `pc_music_control.py`（插件） | 搬进来 |
| M8 | `run_powershell` | pc_agent ← `run_shell`+`run_powershell` | 二合一，**白名单** |
| M8 | `web_fetch` | pc_agent | 搬；亦可交 Playwright MCP |
| M8 | `search_web` | pc_agent | 搬（Firecrawl/Tavily） |

**保持在外、不进 MCP 的**（见 docs/62 第三节）：
主动播报 M9、到点提醒 M10、会话管道 M12、固件 M13。

---

## 三、分阶段可执行清单

### 阶段一 · 通道打通（不动现有系统）
- [ ] 生成 `tools/__init__.py` 注册表，把现有 12 个工具挪进去（行为零变化）
- [ ] 跑 `python bridge/selftest_stdio.py`，确认**工具齐全 + description 非空**
- [ ] 在上游 `.config.yaml` 里挂 `server_mcp`（同机 stdio）**或** `mcp_endpoint`（跨机）
- [ ] 与现有插件**双跑观察**，记录命中率

### 阶段二 · 工具迁移
- [ ] 把 `pc_agent.py` 的 17 个工具按上表搬进 `tools/*.py`
- [ ] 合并 `read_text_file` 与 `dispatch_agent` 的重复实现
- [ ] 评估用开源 MCP 替换：**Windows Control**（desktop）、**Everything Search**（search_files）
- [ ] 迁移完成后，从 `plugins_func/functions/` 删掉对应插件

### 阶段三 · 搬不走的部分
- [ ] 主动播报/提醒 → 重写为上游插件（`conn` 可用，能主动发 TTS）
- [ ] 管道类改动整理成 `patches/` + 升级后自动重放脚本
- [ ] 固件板级目录独立管理；通用修复推上游 PR

---

## 四、迁移时必须守的纪律

| # | 纪律 | 原因 |
|---|---|---|
| 1 | **工具 docstring 必须是普通字符串，不能是 f-string** | 否则 description 是空串，模型乱选工具（`server_agent.py` 顶部已标为铁律一） |
| 2 | **工具返回字符串是"念给主人听的话"** | 口语化，别堆 JSON（铁律二） |
| 3 | **一次不要挂超过 7 个工具组** | 工具列表过长会降低选择准确率 |
| 4 | **写文件/执行命令类工具必须加白名单** | MCP 工具暴露的就是本机权限 |
| 5 | **迁移期两套并存要防双写** | 比如记忆：`memory.py` 与 MCP 版只能有一个写入方 |

---

## 五、一句话

**`bridge/` 已经是一个能跑的 MCP Server，我们缺的只是"把另外 17 个工具并进去 + 把服务端对应插件删掉"。**
