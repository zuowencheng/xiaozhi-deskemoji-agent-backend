# bridge · 本机 Windows 桥接服务

把小智云端（xiaozhi.me）的 MCP 接入点接到你这台电脑，让语音指令能真正操作本机。

```
小智硬件 → xiaozhi.me 云端（ASR+LLM+TTS） → wss MCP 接入点
        → mcp_pipe.py（本进程，wss↔stdio 哑管道）
        → server_agent.py（本机 MCP Server，注册工具）
        → 你的电脑（状态/进程/应用/文件/任务）
```

## 第一次使用

1. **安装依赖**（只需一次）：双击 `install.cmd`
   > 或者手动：`pip install -r requirements.txt`

2. **配置接入点**：把 `.env.example` 复制成 `.env`，把小智控制台里的 MCP 接入点地址（`wss://...`）填进 `MCP_ENDPOINT`
   > ⚠️ `.env` 已在 `.gitignore` 里，**永远不会进仓库**

3. **启动**：双击 `start.cmd`
   > ⚠️ 这个窗口**必须保持开着**，关掉桥就断了。
   > ⚠️ 必须由**你自己双击启动**——AI 沙箱里拉起的进程会被回收，我帮不了你挂后台。

4. **验证**：看到 `[pipe] connected -> wss://api.xiaozhi.me/mcp/` 说明链路通了；
   然后对小智说话试试：「小智，看看我电脑现在卡不卡」

## 当前可用工具

| 工具 | 作用 | 语音示例 |
|---|---|---|
| `get_pc_status` | CPU/内存/磁盘/电池/开机时长 | 「电脑现在卡不卡」「还有多少电」 |
| `list_running_apps` | 占内存最高的进程 | 「电脑在忙什么」 |
| `open_app` | 启动白名单应用 | 「打开记事本」「开个计算器」 |
| `read_text_file` | 读文本文件内容 | 「把 D 盘那个笔记念给我听」 |
| `dispatch_agent` | 登记任务给本机助理 | 「让助理帮我整理一下项目进度」 |
| `query_task` / `list_recent_tasks` | 查任务结果 | 「刚才那活干完没」 |

## 预留（尚未接入 · RESERVED）

| 文件 | 说明 |
|---|---|
| `archive_client.py` | **个人知识库只读客户端**——查成哥 20 年工作资料（本地服务 `http://127.0.0.1:8765`）。**尚未注册成 MCP 工具**，可独立运行自测：`python archive_client.py search 重庆应指项目` |
| `../docs/13-与个人知识库打通预留.md` | 打通时机、形态、与 `memory.py` 的分工、两个必须知道的坑 |

接进来之前**先读 docs/08** —— 尤其是「**嵌入模型不一致，不要跨库比较向量**」那条。

## 排障

| 症状 | 原因 / 处理 |
|---|---|
| `MCP_ENDPOINT 未设置` | 没建 `.env` 或没填地址 |
| 一直 `disconnected ... retry` | token 过期（控制台重新生成）、或网络不通 |
| 工具被乱选 / 行为诡异 | 先看 `tools/list` 里 description 是否为空——**docstring 写成 f-string 会导致 description 空串** |
| 双击一闪而过 | 命令行报错被吞，改用 PowerShell 手动跑 `python mcp_pipe.py server_agent.py` 看报错 |
| 中文输出乱码 | 子进程 stderr 是 cp936，代码里已用 `errors="replace"` 兜住 |

## 自测（不连云端也能验）

```bash
python selftest_stdio.py
```
会发 `initialize` + `tools/list`，判据：**工具齐全 + description 非空**。
