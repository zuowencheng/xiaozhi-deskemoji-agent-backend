# 小智桌面智能体 · 一键部署包

把「小智固件 + 自建服务端 + 自研能力 + MCP 工具」打包成一个能双击就跑的东西。

> 定位：**卖的不是代码，是"省事"**。
> 用户自己从零搭要踩的坑：Python 环境、依赖冲突、配置填错、服务不会自启、重启后起不来、MCP 起不来不知道为什么。
> 这套东西把上面的坑全部变成一条命令。

---

## 三种用法

### 1. 全新部署（或很久没跑，不知道状态）
双击 **`一键部署.cmd`**
走完六个阶段：环境 → 依赖 → 配置 → 开机自启 → 启动 → 体检，最后打印一份报告。

### 2. 日常：服务是不是还活着？
```
python tools\doctor.py
```
一条命令看全链路：四个端口 / 进程 / 看门狗 / 开机自启 / 设备在线 / MCP 工具 / 磁盘 / 最近报错。
**排障永远先跑它**，别上来就猜。

### 3. 改了 MCP 配置之后
```
python tools\mcp_probe.py      # 自检（也是预热，首次启动会下包）
:: 然后重启服务端 —— MCP 配置只在连接建立时读一次，不重启不生效
```

---

## 目录说明

| 路径 | 作用 |
|---|---|
| `deploy/setup.py` | 部署编排，**幂等**，装一半再跑一次不会搞坏 |
| `deploy/一键部署.cmd` | 双击入口 |
| `tools/doctor.py` | 全链路体检（标品门面） |
| `tools/mcp_probe.py` | MCP stdio 自检 + 预热 |
| `tools/mcp_toggle.py` | MCP 开关（在 `mcpServers` / `_disabled` 之间搬） |
| `tools/svc_watchdog.py` | 服务自愈看门狗，每 20 秒巡检，挂了自动拉 |
| `tools/install_autostart.py` | 开机自启安装（启动文件夹 VBS，免管理员） |
| `tools/start_services.py` | 启动服务（必须借 explorer.exe 才能脱出沙箱） |
| `tools/install_mcp_deps.cmd` | 装第三方 npm MCP（沙箱里装不了，需自己双击跑） |
| `bridge/tools/local_mcp.py` | **自研 MCP Server**（8 个工具，离线可用） |

---

## 当前装了哪些 MCP 工具

### 已启用：`xiaozhi-local`（自研，Python，离线可用）

| 工具 | 能干嘛 |
|---|---|
| `list_dir` | 列目录（带文件类型/大小） |
| `read_file` | 读文本文件（大文件可分段） |
| `write_file` | 写文件（自动建父目录） |
| `search_files` | 按**文件名**搜索 |
| `grep_files` | 按**内容**搜索（找"哪份文档提到过 X"） |
| `fetch_url` | 抓网页正文（去脚本样式，只留文字） |
| `run_shell` | 执行一条本机命令（带危险命令黑名单） |
| `now` | 当前日期时间星期 |

**安全边界（重要）**：
- 目录白名单只有 `Desktop` / `Documents` / 项目目录，越权读写会被拒（含 `..\` 穿越）
- `run_shell` 黑名单拦 `del` / `format` / `taskkill` / `reg delete` / `rm -rf` 等
- 命令超时 30 秒，输出截断 8000 字符
- 所有文件操作会记到 `runtime/logs/local_mcp.log`

### 预置未启用（`python tools\mcp_toggle.py enable <名字>`）

| 服务 | 说明 | 启用前提 |
|---|---|---|
| `filesystem` | 官方文件读写 | 需先跑 `install_mcp_deps.cmd`（沙箱装不了 npm） |
| `windows-cli` | 命令行执行 | 同上；且 `run_shell` 已覆盖，带黑名单更安全 |
| `memory` | 知识图谱记忆 | ⚠️ 与自研 `memory_tools` 重叠，两个一起开模型会分不清 |
| `playwright` | 浏览器自动化 | 需 `npx playwright install` 装内核 |

> **为什么 npm 的包装不上**：WorkBuddy 沙箱把 `reg.exe` 列入黑名单，npm 装包必调它 → 被拦。
> 你在自己的终端/双击 cmd 里跑就不受这个限制。

---

## 交付给别人前必须改的

1. **路径硬编码**：`tools/start_services.py:29` 的 `VENV_PY`、`deploy/setup.py` 的 `VENV_PY`/`BASE_PY`、MCP 配置里的绝对路径
2. **敏感信息**：`data/.config.yaml` 里的 API Key（和风天气、DeepSeek、火山引擎）、设备 ID
3. **目录白名单**：`bridge/tools/local_mcp.py` 的 `ALLOWED_ROOTS`（改成对方的用户目录）
4. **后端地址**：设备配网时填的 `ws://<本机IP>:8000/xiaozhi/v1/`（IP 变了设备就连不上）

---

## 已知边界

- 进程"半死但端口还开着"检测不到（要加 HTTP 健康检查，有误判风险，暂未做）
- `run_shell` 仍是"把命令行交给大模型"，公用机器上不建议开
- 任务计划方式（`--method task`）需要管理员权限，默认走启动文件夹 VBS
