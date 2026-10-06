# 67 · Plan B：fork 压薄方案 + 能力迁移勾选清单

> 主人决策（2026-10-05）：**选 Plan B** —— PR 若被拒/挂起，把 fork 压成极薄 patch，上游每发版 rebase 一次。
> 补充事实：**板子是买的**（不是自焊原型）→ 满足上游"可复现"硬条件，PR 仍为主线，Plan B 是兜底。

---

## 一、现状：21 文件 / +806 −162

按"切到官方上游后还需不需要自己扛"重新分类：

| 类 | 文件 | 行数 | 切上游后 |
|---|---|---|---|
| 🟩 **A 板级目录**（核心资产） | `boards/esp32-s3n16r8-emoji/` 共 7 个 | +161 | **必须留**，PR 目标 |
| 🟨 **B 功能 hook**（我们独有） | `application.cc/.h`、`protocol.cc/.h`、`websocket_protocol.cc` | +224 | **必须留**，压薄对象 |
| 🟨 **B** | `audio/audio_service.cc/.h`（提示音白名单） | +77 | 必须留 |
| 🟦 **C 通用 bug 修复** | `display/oled_display.cc`（LVGL 冻结） | +25 | **可独立 PR 甩掉** |
| 🟦 **C** | `websocket_protocol.cc`（`on_connected_` 补发） | +10 | **可独立 PR 甩掉** |
| 🟥 **D 本地化**（⭐ 白捡） | `assets/lang_config.h` vi-VN→zh-CN | ±118 | **切官方后自动消失** |
| 🟥 **D** | `popup.ogg` / `listening.ogg` 音效 | Bin | 切官方后重做即可 |
| ⬛ **E 调试代码** | `audio_service.cc` 唤醒诊断任务（30s 打一行） | +30 | **现在就能删** |
| ⬛ **E 调参** | `afe_audio_processor.cc` `vad_min_noise_ms=1400` | ±8 | **改 Kconfig 或接受上游默认** |
| ⚪ F 配置 | `sdkconfig` / `.defaults` / `dependencies.lock` | +341 | 跟着走，不算负担 |

### ⭐ 白捡的一条：lang_config.h

我们的 fork 上游是 **TranBinh2311（越南作者）**，默认语言是 `vi-VN`。那 ±118 行是**把越南语翻译成中文**。
切回 **78/xiaozhi-esp32 官方**后默认就是 zh-CN —— **这 118 行直接归零，不用维护**。

同理 `popup.ogg`（fork 自带的机械提示音）也是 fork 特有，官方有自己的音效。

---

## 二、Plan B 目标形态：从 21 文件压到 **4 个接触点**

```
官方固件（未改动）
├── main/boards/esp32-s3n16r8-emoji/     ← ① 新增目录，零冲突
├── main/application.cc / .h              ← ② 2 处 hook（约 40 行）
├── main/protocols/protocol.{h,cc}        ← ③ 1 个字段透传（约 15 行）
└── main/audio/audio_service.{cc,h}       ← ④ 提示音白名单（约 40 行）
```

**预期 rebase 成本**：上游不动 `application.cc` 的聆听/播报分支时 → **零冲突，直接 apply**。
真冲突了也只是这 4 处，不是 21 处。

---

## 三、压薄动作清单（按顺序，每步独立可回滚）

| # | 动作 | 收益 | 风险 |
|---|---|---|---|
| 1 | 删 `audio_service.cc` 的唤醒诊断任务（调试代码已完成使命） | −30 行 | 无 |
| 2 | `vad_min_noise_ms` 改成 `sdkconfig` 项，不再改源码 | −1 文件 | 低（等价替换） |
| 3 | `oled_display.cc` LVGL 修复 → **独立 PR 给官方** | 甩掉 1 文件 | 无（不收就留着，仅 25 行） |
| 4 | `websocket_protocol.cc` `on_connected_` → **独立 PR 给官方** | 甩掉 10 行 | 同上 |
| 5 | `lang_config.h` / 音效资产 → **不做**（切官方时自然消失） | −118 行 | 无 |
| 6 | 板级目录单独提 PR（附购买链接 + 实物照 + 接线表） | 主线目标 | 需等审核 |
| 7 | 剩余 4 处 hook 打成 `firmware/patches/thin-*.patch` | 可重放 | 低 |

> 第 1、3、4 步**现在就能做**，做完 21 文件 → 17 文件。
> 第 6 步无论成败，第 7 步都要做。

---

## 四、能力迁移勾选清单（请圈号）

### 已上线 ✅（`bridge/tools/local_mcp.py`，2026-10-05 已接入，日志确认 8 个工具）

| # | 工具 | 能力 |
|---|---|---|
| 1 | `list_dir` | 看目录 |
| 2 | `read_file` | 读文件 |
| 3 | `write_file` | 写文件 |
| 4 | `search_files` | 按文件名搜 |
| 5 | `grep_files` | 按内容搜 |
| 6 | `fetch_url` | 抓网页 |
| 7 | `run_shell` | 执行命令 |
| 8 | `now` | 日期时间 |

### ✅ 已上线 第一批（能力 9~22，2026-10-05 22:58）

主人圈号 `9-22` → 新建 **`bridge/tools/pc_mcp.py`**（独立 MCP Server，14 个工具）。

**实现方式：不重写，全部 import 复用现成实现**（零行为漂移）
- M1/M4（9,10,11,19,20,21,22）← `bridge/server_agent.py`
- M2/M3（12~18）← `core/pc_agent.py` → `core/pc_desktop.py`

**验证（硬证据）**
- `--selftest` 14 个工具 description 全非空；实调用真实数据全通：
  `get_pc_status` → CPU 0.8%/内存 45.4%/C 盘剩 282GB；
  `list_windows` → 13 个窗口；`organize_dir`（dry-run）→ 桌面 10 个文件归类计划
- `mcp_probe` 两个 Server 都握手成功（8 + 14 = 22 工具）
- 服务端日志 `22:58:44 服务端MCP客户端已连接，可用工具: ['get_pc_status', ...]`
- 服务端「当前支持的函数列表」已含 `get_pc_status` / `list_windows` / `screenshot` / `organize_dir` / `dispatch_agent`
- 设备 22:58:42 自动重连

### ⚠️ 已上线但存在并存隐患（待主人拍板）

**插件版与 MCP 版派单工具同时存在**：

| 版本 | 工具 | 来源 |
|---|---|---|
| 插件版 | `pc_run_task` / `pc_query_task` / `pc_clean_workdir` | `plugins_func/functions/pc_control.py` |
| MCP 版 | `dispatch_agent` / `query_task` / `list_recent_tasks` / `get_pending_results` | `bridge/tools/pc_mcp.py` |

两者底层都写 `bridge/inbox/`，功能等价。风险：LLM 看到两个"派任务"工具 → **可能选错或重复派单**（一件事干两遍）。

**建议**：等主人回来后关掉插件版（从 `.config.yaml` 的 `functions` 列表移除 `pc_control`）。
⚠️ 注意 `functions` 是**列表**，递归合并会整体覆盖，改之前要读现有的完整列表。
**本轮未动**（深夜改配置风险大，且会让设备断开重连）。

### 🔜 待搬（第二批 / 第三批）

| 模块 | 编号 | 工具 |
|---|---|---|
| M5 记忆 | 23-26 | `remember` `recall` `list_memories` `forget` |
| M6 待办 | 27-30 | `todo_add` `todo_list` `todo_done` `todo_del` |
| M7 音乐 | 31-34 | `music_play` `music_pause` `music_next` `music_vol` |
| M8 网络 | 35 | `search_web` |

> 之前说"24 个"是估算，实际清点 **35 个**（8 已上线 + 27 待搬）。

### ⚠️ 迁移纪律（docs/63 第四节）

1. 工具 docstring **不能是 f-string**，否则 description 为空，模型乱选工具
2. 返回值是"念给你听的话"，口语化，别堆 JSON
3. **一次挂不超过 7 个工具组**，挂太多会降低模型选工具准确率 → 建议分批
4. 写文件/执行命令**必须白名单**
5. 迁移期两套并存**防双写**（记忆尤其危险）

---

## 五、建议的搬迁批次

| 批次 | 圈号范围 | 理由 |
|---|---|---|
| **第一批（本周）** | 9~16（系统+桌面）+ 19~22（任务） | 你日常最高频，且已有一半实现现成 |
| 第二批 | 23~30（记忆+待办） | 有插件可直接搬，但**要处理双写** |
| 第三批 | 17、18、31~35 | 低频或高风险，`organize_dir` 务必带 dry-run |

---

## 六、一句话

**Plan B 的真正价值不是"补丁更薄"，是把「21 个文件的认知负担」变成「4 个接触点 + 1 个独立目录」。**
而且切官方时 `lang_config.h` 那 118 行白捡——因为它们是"把越南语翻成中文"，官方本来就是中文。
