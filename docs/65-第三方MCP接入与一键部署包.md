# 65 · 第三方 MCP 接入 + 一键部署包（标品）

> 2026-10-05 凌晨。主人诉求：①接入别人的 MCP，扩充能力 ②把"一键部署包"做成标品
> 前置：`docs/62` 评估、`docs/63` 蓝图、`docs/64` 整合路线

---

## 一、结论先行

- ✅ **自研 MCP `xiaozhi-local` 已上线**，8 个工具，服务端日志确认加载成功
- ⚠️ **第三方 npm MCP 装不上**——沙箱把 `reg.exe` 列入黑名单，npm 装包必调它 → 被拦。
  已写好 `tools/install_mcp_deps.cmd`，主人自己双击就能装
- ✅ **一键部署包成型**：`deploy/`（setup.py + cmd + README + 模板）、`tools/doctor.py`（体检）

---

## 二、踩到的坑（都是硬知识，下次别重复踩）

### 坑 1：npx 启动太慢，服务端初始化只有 10 秒

`mcp_manager.py:57` 给每个 MCP 的初始化超时是 **10 秒**。
而 `npx -y <pkg>` 每次启动都要**联网校验版本**，本机到 npm registry 很慢——实测 **120 秒都没起来**，
probe 报的错是 `ExceptionGroup → TimeoutError`（真因藏在第三层，必须递归展开才看得到）。

→ 结论：**npx 类 MCP 在这台机器上不可用**，除非先 `npm install` 到本地再用 node 直跑入口 js。

### 坑 2：npm install 被沙箱拦

```
PROGRAM BLOCKED BY SECURITY POLICY - reg.exe (C:\Windows\System32\reg.exe)
```
npm 装包会调 reg.exe 读配置 → 沙箱黑名单 → **装不了，无法绕过**。
已试过官方源和 npmmirror 镜像，都在同一个地方被拦。

→ 结论：第三方 npm MCP 只能由主人在自己终端/双击 cmd 里装。已提供 `tools/install_mcp_deps.cmd`。

### 坑 3：两个常用 MCP 包已经从 npm 下架

| 包 | 结果 |
|---|---|
| `@modelcontextprotocol/server-fetch` | **E404 不存在** |
| `@modelcontextprotocol/server-time` | **E404 不存在** |
| `@modelcontextprotocol/server-git` | **E404 不存在**（`mcp-server-git` 是 0.0.1-security 占位包，别用） |
| `@modelcontextprotocol/server-memory` | ✅ 2026.8.31 可用 |
| `@modelcontextprotocol/server-filesystem` | ✅ 2026.8.31 |
| `@simonb97/server-win-cli` | ✅ 0.2.1 |
| `@executeautomation/playwright-mcp-server` | ✅ 1.0.12 |

→ fetch / time 的缺口由**自研 MCP 补齐**（`fetch_url` / `now`）。

### 坑 4：Everything 没装 es.exe

`C:\Program Files\Everything\es.exe` 不存在 → `everything_search` 工具**不注册**（优雅降级，不报错）。
以后装了 Everything 会自动多出一个工具。

### 坑 5：stdio 模式下不能往 stdout 打任何东西

MCP stdio 通道上 stdout 就是 JSON-RPC 协议流，**print 一下就会握手失败**。
`local_mcp.py` 所有诊断信息一律写 `runtime/logs/local_mcp.log`。

### 坑 6：配置改了不重启不生效

`mcp_manager` 在**连接建立时**才读配置。改完 `.mcp_server_settings.json` 必须重启服务端。

---

## 三、自研 MCP：`bridge/tools/local_mcp.py`

| 工具 | 说明 |
|---|---|
| `list_dir` | 列目录（类型 + 大小） |
| `read_file` | 读文本（大文件可 head_lines 分段） |
| `write_file` | 写文件（自动建父目录，支持 append） |
| `search_files` | 按**文件名**搜索 |
| `grep_files` | 按**内容**搜索 |
| `fetch_url` | 抓网页正文（补 npm fetch 下架的缺口） |
| `run_shell` | 执行一条命令（带黑名单） |
| `now` | 日期时间星期（补 npm time 下架的缺口） |

**安全边界**（自测已验证真生效，不是纸面上的）：
- 目录白名单：`Desktop` / `Documents` / 项目目录；越权和 `..\` 穿越都被拒
- 命令黑名单：`del` / `format` / `taskkill` / `reg delete` / `rm -rf` / `diskpart` 等，实测三条危险命令全拦
- 命令超时 30s、输出截断 8000 字符
- 写文件留痕到 `runtime/logs/local_mcp.log`

**为什么自研反而更好**：零网络依赖、离线可用、可控可审计、有安全边界，
而且是**我们自己的资产**（能进标品、能卖）。

---

## 四、一键部署包（标品）

| 文件 | 作用 |
|---|---|
| `deploy/setup.py` | 六阶段编排，**幂等**，装一半再跑不会搞坏；支持 `--check-only` / `--skip-deps` |
| `deploy/一键部署.cmd` | 双击入口（CRLF 已修） |
| `deploy/README.md` | 标品说明、MCP 清单、交付给别人要改什么 |
| `deploy/templates/.mcp_server_settings.json` | 配置模板（无敏感信息） |
| `tools/doctor.py` | **全链路体检**：端口/进程/看门狗/自启/设备/MCP/磁盘/最近报错，一条命令出结论 |
| `tools/mcp_probe.py` | MCP stdio 自检（兼作预热），递归展开 ExceptionGroup |
| `tools/mcp_toggle.py` | 在 `mcpServers` 和 `_disabled` 之间搬（上游没有 disabled 字段，只能这么设计） |
| `tools/install_mcp_deps.cmd` | 给主人手动装 npm MCP 用（走 npmmirror 镜像） |

---

## 五、验证记录（硬证据）

| 时间 | 事件 |
|---|---|
| 02:48 | `local_mcp.py --selftest` 全过：白名单拦截 + 穿越拦截 + 3 条危险命令拦截 + 文件读写往返 |
| 02:49:39 | 配置写入 + `mcp_probe.py` 握手成功，8 个工具 |
| 02:49:49 | 杀掉 app.py → 看门狗自愈拉起（PID 12696）→ **设备 02:49:49 自动重连** |
| 02:49:52 | 日志：`服务端MCP客户端已连接，可用工具: ['list_dir', ..., 'now']` |
| 02:52 | `doctor.py` 体检：**全链路正常 ✅** |

---

## 六、发现的其他问题（待处理）

1. **天气插件 API Key 失效**：`get_weather` 报 `Authentication failed, check your KEY/Token or Host`（和风天气）
2. ASR 偶发 `Timeout waiting next packet`（8 秒），属正常断句结束，暂不处理
3. `doctor.py` 里"python 进程正常是 2 个"这句不准（本机有 13 个，含其他用途），后续改成只统计小智相关

---

## 七、下一步

- [ ] 主人回来**实测新工具**：对小智说"我桌面上有什么文件"、"帮我看看这个链接"、"现在几点"
- [ ] 手动跑 `tools/install_mcp_deps.cmd` 装 npm MCP（filesystem / win-cli / memory / playwright）
- [ ] 换掉失效的和风天气 Key
- [ ] 继续 `docs/64` 的阶段 1：把自研 24 个能力整理进 MCP
