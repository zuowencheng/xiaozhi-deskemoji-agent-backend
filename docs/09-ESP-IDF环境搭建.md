# 09 · ESP-IDF 环境搭建（Windows · EIM 官方安装器）

> 搭建日期：2026-10-02　　安装器：EIM（Espressif.EIM-CLI）v0.19.0，winget 安装
> 结果：**v5.5.4 与 v6.1 双版本装好并验证通过**

## 一、装上了什么

| 项 | 值 |
|---|---|
| 安装根目录 | `C:\Espressif` |
| 已装版本 | **v5.5.4** ＋ **v6.1**（EIM 多版本共存，当前默认选中 **v6.1**） |
| 目标芯片 | `esp32s3` + `esp32`（工具链共用，经典 ESP32 也能编） |
| 占用 | 共约 **18 GB**（`tools` 16 GB 两版共用 + v5.5.4 631 MB + v6.1 742 MB） |
| 自带 Python | `C:\Espressif\tools\python\<版本>\venv\Scripts\python.exe` |
| 安装配置留档 | 项目根 `eim_config.toml`（EIM 自动生成，可复现安装） |

工具链清单（`C:\Espressif\tools`）：`xtensa-esp-elf`、`xtensa-esp-elf-gdb`、`riscv32-esp-elf`、`esp-clang`、`esp-clang-libs`、`esp-clangd`、`cmake`、`ninja`、`openocd-esp32`、`ccache`、`dfu-util`、`esp32ulp-elf`、`esp-rom-elfs`、`idf-exe`、`python`、`components`。

## 二、验证结果（两个版本都已通过）

| 版本 | `idf.py --version` | `IDF_PATH` | 激活脚本 |
|---|---|---|---|
| v5.5.4 | `ESP-IDF v5.5.4` | `C:\Espressif\v5.5.4\esp-idf` | `Microsoft.v5.5.4.PowerShell_profile.ps1` |
| **v6.1** | **`ESP-IDF v6.1.0`** | `C:\Espressif\v6.1\esp-idf` | `Microsoft.v6.1.PowerShell_profile.ps1` |

```
$ eim list
Installed versions:
- v5.5.4 [ok] [C:\Espressif\v5.5.4\esp-idf]
- v6.1 (selected) [ok] [C:\Espressif\v6.1\esp-idf]
```

- 桌面快捷方式：`IDF_v5.5.4_Powershell.lnk`、`IDF_v6.1_Powershell.lnk`
- 版本登记：`C:\Espressif\tools\eim_idf.json`

## 三、怎么用

### 1) 最省事：双击桌面快捷方式
- `IDF_v6.1_Powershell.lnk` → v6.1（**官方 v2.5.0 主干用这个**）
- `IDF_v5.5.4_Powershell.lnk` → v5.5.4（编社区版/老工程用这个）

打开就是**已激活**的终端，直接敲 `idf.py` 即可。

### 2) 手动激活（PowerShell）
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
. 'C:\Espressif\tools\Microsoft.v6.1.PowerShell_profile.ps1'     # 或 .v5.5.4.
idf.py --version
```
> 激活脚本**只有 PowerShell 版**（`.ps1`），没有 .bat —— 本机执行策略是 Restricted，所以必须先做进程级 Bypass（不改系统策略、不需要管理员）。

### 3) 常用命令
```powershell
idf.py set-target esp32s3      # 设定目标芯片（经典 ESP32 用 esp32）
idf.py menuconfig              # 图形配置
idf.py build                   # 编译
idf.py -p COM3 flash monitor   # 烧录 + 串口监视
```

### 4) 多版本管理
```powershell
eim list      # 看已装版本
eim select    # 切换默认版本
```

## 四、本次实际用的安装命令（可复用）

**v5.5.4**（源码走 jihulab，成功）：
```bash
eim install \
  -p 'C:\Espressif' -i v5.5.4 -t esp32s3 \
  -m 'https://ghfast.top/https://github.com' \
  --idf-mirror 'https://jihulab.com/esp-mirror' \
  --pypi-mirror 'https://mirrors.aliyun.com/pypi/simple' \
  -l cn -n true --cleanup true --do-not-track true
```

**v6.1**（jihulab 没有 v6.1 标签，源码也走 ghfast，成功）：
```bash
eim install \
  -p 'C:\Espressif' -i v6.1 -t esp32s3,esp32 \
  -m 'https://ghfast.top/https://github.com' \
  --idf-mirror 'https://ghfast.top/https://github.com' \
  --pypi-mirror 'https://mirrors.aliyun.com/pypi/simple' \
  -l cn -n true --cleanup true --do-not-track true
```

## 五、踩坑记录（下次直接抄）

1. **`-m/--mirror` 的语义是"替换 URL 里的 `https://github.com` 前缀"**，不是填一个镜像站地址。
   填独立站地址（如 `https://dl.espressif.cn/dl/`）会拼出死链，装到一半就断。
   正确写法 = GitHub 加速代理前缀：`https://ghfast.top/https://github.com`。
2. **GitHub 直连被 reset**（`git ls-remote github.com` 直接 Connection was reset），必须走加速代理。
   实测 `ghfast.top` / `gh-proxy.com` / `ghproxy.net` 三个都通。
3. **`--idf-mirror` 用 jihulab**：`https://jihulab.com/esp-mirror` → `jihulab.com/esp-mirror/espressif/esp-idf.git` ✓
   ⚠️ **但 jihulab 是滞后的**：只有到 `v6.0.1`，**没有 `v6.1` 标签** → 装 v6.1 必须换源（见第 4 条）。
4. **装 v6.1 时 `--idf-mirror` 也得用 ghfast**：`https://ghfast.top/https://github.com`。
5. **gix 报错会自动回退系统 git**：装 v6.1 时出现过
   `ERROR - Submodule update failed ... An IO error occurred when talking to the server`
   → 紧接着 `WARN - gix-based clone ... failed; falling back to system git`，
   **回退后照常拉完**。别看到 ERROR 就以为失败，认 `Successfully installed IDF` 和 `EXIT=`。
6. **别加 `-v`**：DEBUG 日志上百万行，输出会被截断、还拖慢安装。
7. **`--log-file` 必须传 Windows 路径**（`C:\...`），传 Unix 路径（`/c/...`）会静默失败、日志丢失。
8. EIM 会先做 Python 自检，本机系统 Python 被判为 "missing"，它**自带 Python 3.13**装在 `C:\Espressif\tools\python`，不用管。
9. **安装期间不要结束会话回合**：后台任务在回合结束时会被回收，安装会无声中断（日志停在某行、无 `EXIT=`）。本回合内持续轮询才跑完。
10. **`api.github.com` 是直连可用的**（只有 `github.com` 的 git/HTTPS 被 reset）——
    查版本、查板型目录直接打 API 即可，不必绕代理。

## 六、已知警告（都是无害的）

### 1) v5.5.4
```
WARNING: Git version unavailable, reading from source
```
EIM 在 `eim_idf.json` 里登记的 `gitPath` 指向 `.workbuddy` 下的 PortableGit，读 git 版本失败后**回退读源码目录**，不影响编译。

### 2) v6.1 —— 控制台编码
```
Your environment is not configured to handle Unicode characters.
Recommended fix (persistent):
 - Enable "Beta: Use Unicode UTF-8 for worldwide language support" in Windows language/region settings.
Workarounds:
 - Run "chcp 65001" in the current console before running idf.py.
 - Set PYTHONUTF8=1 before running idf.py.
```
不影响 `idf.py --version`（已正常输出 `ESP-IDF v6.1.0`），但**编中文注释的工程时建议先 `chcp 65001` 或设 `PYTHONUTF8=1`**，避免中文路径/日志乱码。

## 七、下一步（回到 08 号文档的两段计划）

- **Stage A**：用 **v5.5.4** 编**社区版固件** → 验证工具链、复现现固件、把"编译→刷机→回滚"走通
- **Stage B**：用 **v6.1** 编**官方 v2.5.0 + 移植后的 deskemoji 板型** → 长期唯一主干

详见 `08-第二块板与IDF版本路线.md`。
