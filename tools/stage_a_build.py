# -*- coding: utf-8 -*-
"""
Stage A · 编一版"指向本机自建后台"的社区固件
=============================================

背景
----
板子上现在跑的固件（xiaozhi 2.0.1 / ESP-IDF 5.5.1）里写死了 xiaozhi.me 的
OTA 地址，所以设备永远去找云，不来找本机后台（192.168.3.139:8003）。

Stage A 的思路是**最小改动**：拿目前板子上这版固件对应的社区源码
（TranBinh2311/xiaozhi-deskemoji-gesture-music-taibai），**只改 OTA 地址**
指向本机，重编一版刷进空着的 ota_1。手势/表情/音乐全都不动。

流程（每一步都会打印，失败即停）
--------------------------------
  1. 浅克隆社区源码 -> firmware/community        （GitHub 直连不通，走加速代理）
  2. 从 main/Kconfig.projbuild **自动识别** OTA 地址的配置项名（不靠猜）
  3. 把它写进 sdkconfig.defaults
  4. **自动识别**板型开关（BOARD_TYPE_*）并写进 sdkconfig.defaults
  5. 用 ESP-IDF v5.5.4 的激活脚本 + idf.py 构建
  6. 产物 build/xiaozhi.bin；加 --flash 则写进 ota_1（otatool，不动 ota_0）

用法
----
    python tools/stage_a_build.py                  # 只编译
    python tools/stage_a_build.py --flash COM3     # 编译 + 写 ota_1
    python tools/stage_a_build.py --ota-url http://192.168.3.139:8003/xiaozhi/ota/
    python tools/stage_a_build.py --source-existing   # 跳过克隆，用已有源码

注意事项
--------
* **绝对不要**用 idf.py flash / app-flash —— 那会写 ota_0（现在能跑的那版）。
  写 ota_1 必须用 otatool，或者手动 esptool 写偏移 0x500000。
* ota_1 写完之后**不会自动启动**；需要切分区才生效（见 docs/16）。
"""

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
FW_DIR = os.path.join(PROJECT, "firmware")
SRC_DIR = os.path.join(FW_DIR, "community")

REPO = "TranBinh2311/xiaozhi-deskemoji-gesture-music-taibai"
CLONE_PROXIES = [
    "https://ghfast.top/",
    "https://gh-proxy.com/",
    "https://ghproxy.net/",
    "",
]

BOARD_DIR_NAME = "esp32-s3n16r8-emoji"
BOARD_TOKEN = "EMOJI"          # 用来在 BOARD_TYPE_* 里认板子
DEFAULT_OTA_URL = "http://192.168.3.139:8003/xiaozhi/ota/"

IDF_PROFILE = r"C:\Espressif\tools\Microsoft.v5.5.4.PowerShell_profile.ps1"
IDF_TARGET = "esp32s3"


def log(msg=""):
    print(msg, flush=True)


def step(n, msg):
    log("")
    log("=" * 66)
    log(f"[STEP {n}] {msg}")
    log("=" * 66)


def die(msg, code=2):
    log(f"[FAIL] {msg}")
    sys.exit(code)


def read_text(p):
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def write_text(p, s):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def clean_env():
    """剥掉从 Git Bash 继承来的 MSys 变量。

    ⚠️ 这是 Stage A 第一次失败的真因（2026-10-02）：
    ESP-IDF v5.5.4 的 tools/idf.py 里（第 914 行）
        if 'MSYSTEM' in os.environ:
            print_warning('MSys/Mingw is no longer supported. ...')
        elif ...:
            ...
        else:
            main()
    这个分支里**没有 main()** —— 也就是说只要 MSYSTEM 存在，idf.py
    打印一句警告后就直接退出，什么都不编，**退出码还是 0**。
    从 Git Bash 里拉起 python -> powershell -> idf.py 时，MSYSTEM=MINGW64
    会被一路继承下来，于是"构建成功但找不到产物"。
    """
    env = dict(os.environ)
    for k in ("MSYSTEM", "MSYSCON", "MINGW_PREFIX", "MINGW_CHOST",
              "MINGW_HOME", "PLATFORM"):
        env.pop(k, None)
    return env


def run(cmd, cwd=None, env=None):
    log(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, env=env if env is not None else clean_env())


# ------------------------------------------------------------------ 1. 克隆

def clone():
    step(1, f"克隆社区源码 -> {SRC_DIR}")
    if os.path.isdir(os.path.join(SRC_DIR, ".git")):
        log("[SKIP] 已存在，跳过克隆")
        return
    os.makedirs(FW_DIR, exist_ok=True)

    last = None
    for pre in CLONE_PROXIES:
        url = f"{pre}https://github.com/{REPO}.git"
        log(f"--- 尝试: {pre or '直连'}")
        r = run(["git", "clone", "--depth", "1", url, SRC_DIR])
        if r.returncode == 0 and os.path.isdir(os.path.join(SRC_DIR, ".git")):
            log(f"[OK] 克隆成功（{pre or '直连'}）")
            return
        last = r.returncode
        # 清掉半成品再试下一个
        if os.path.isdir(SRC_DIR):
            subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", SRC_DIR])

    die(f"所有镜像都克隆失败（最后一个返回码 {last}）。"
        f"可手动 git clone 到 {SRC_DIR}")


# ------------------------------------------------------- 2/3. OTA 地址识别

def detect_ota_symbol(kconfig_path):
    """从 Kconfig.projbuild 里找出 OTA 地址的配置项名。返回 (symbol, default)。"""
    if not os.path.isfile(kconfig_path):
        die(f"找不到 {kconfig_path}")

    text = read_text(kconfig_path)

    # 优先：definition 后面紧跟 tenclass / ota 默认地址的那个
    blocks = re.split(r"(?m)^\s*config\s+", text)
    candidates = []
    for b in blocks[1:]:
        name = b.split("\n", 1)[0].strip().split()[0] if b.strip() else ""
        if "OTA" not in name.upper():
            continue
        m = re.search(r'(?m)^\s*default\s+"([^"]+)"', b)
        default = m.group(1) if m else ""
        candidates.append((name, default))

    if not candidates:
        return None, ""

    # 有 tenclass/ota 默认地址的那个最靠谱
    for name, default in candidates:
        if "tenclass" in default or "/ota" in default:
            return name, default
    return candidates[0][0], candidates[0][1]


def detect_board_symbol(kconfig_path):
    """找出板型开关，例如 BOARD_TYPE_ESP32_S3N16R8_EMOJI"""
    text = read_text(kconfig_path)
    names = re.findall(r"(?m)^\s*config\s+(BOARD_TYPE_[A-Z0-9_]+)\s*$", text)
    if not names:
        return None, []
    hit = [n for n in names if BOARD_TOKEN in n]
    if hit:
        return hit[0], names
    return None, names


def patch_sdkconfig_defaults(ota_url):
    step(2, "识别 OTA 配置项并写入 sdkconfig.defaults")

    kconfig = os.path.join(SRC_DIR, "main", "Kconfig.projbuild")
    symbol, default = detect_ota_symbol(kconfig)
    if not symbol:
        die("Kconfig.projbuild 里找不到任何 OTA 相关配置项，请人工打开确认")
    log(f"[OK] OTA 配置项: {symbol}   （上游默认值: {default or '未知'}）")
    if default and "tenclass" in default:
        log("     ^ 确认这就是现在写死 xiaozhi.me 的那一项")

    step(3, "识别板型开关")
    board_symbol, all_boards = detect_board_symbol(kconfig)
    if board_symbol:
        log(f"[OK] 板型开关: {board_symbol} = y")
    else:
        log(f"[WARN] 没在 Kconfig 里认出 {BOARD_TOKEN} 板型。可用选项（前 20 个）：")
        for n in all_boards[:20]:
            log(f"       {n}")
        log("       编译时会在 menuconfig 里让你选，或者你也可以用 -DBOARD_NAME= 试试")

    sdk = os.path.join(SRC_DIR, "sdkconfig.defaults")
    content = read_text(sdk) if os.path.isfile(sdk) else ""

    # 先剔除旧的覆盖块（可能是没有 CONFIG_ 前缀的错误版本）
    content = re.sub(
        r"(?ms)^\s*# ---- STAGE_A_OVERRIDE.*?# ---- /STAGE_A_OVERRIDE ----\s*$",
        "",
        content,
    ).rstrip("\n")

    lines = [
        "",
        "# ---- STAGE_A_OVERRIDE: 本机自建后台（由 tools/stage_a_build.py 写入）----",
        # ⚠️ 必须带 CONFIG_ 前缀！sdkconfig.defaults 里不带前缀的行会被 kconfgen
        #    **静默忽略**（不报错、不警告），于是 OTA 地址悄悄退回 Kconfig 里的默认值。
        #    这是 Stage A 第二次失败的真因（2026-10-02）。
        f'CONFIG_{symbol}="{ota_url}"',
    ]
    if board_symbol:
        lines.append(f"CONFIG_{board_symbol}=y")
    lines.append("# ---- /STAGE_A_OVERRIDE ----")
    write_text(sdk, content + "\n" + "\n".join(lines) + "\n")
    log(f"[OK] 已写入 {sdk}")
    for ln in lines[1:]:
        log(f"     {ln}")


def patch_existing_sdkconfig(ota_url):
    """仓库里**自带并 git 跟踪了一个 sdkconfig**（作者把本地构建配置也提交了）。

    ESP-IDF 的取值顺序是：sdkconfig.defaults 打底 -> 已存在的 sdkconfig 覆盖。
    所以只改 sdkconfig.defaults 是**没用的** —— 编译时 OTA 地址仍会取
    sdkconfig 里那行 `CONFIG_OTA_URL="https://api.tenclass.net/xiaozhi/ota/"`。
    必须把这个文件一起改。
    """
    step(4, "改写仓库自带的 sdkconfig（它优先级高于 sdkconfig.defaults）")
    sdk = os.path.join(SRC_DIR, "sdkconfig")
    if not os.path.isfile(sdk):
        log("[SKIP] 没有现成的 sdkconfig —— 首次构建会直接从 defaults 生成")
        return

    old = read_text(sdk)
    m = re.search(r"(?m)^CONFIG_OTA_URL=.*$", old)
    if not m:
        log("[WARN] sdkconfig 里没有 CONFIG_OTA_URL 行，跳过（请人工确认）")
        return
    log(f"[*] 原值: {m.group(0)}")

    new = old[:m.start()] + f'CONFIG_OTA_URL="{ota_url}"' + old[m.end():]

    if not re.search(r"(?m)^CONFIG_BOARD_TYPE_ESP32_S3N16R8_EMOJI=y$", new):
        new = new.replace(
            "# CONFIG_BOARD_TYPE_ESP32_S3N16R8_EMOJI is not set",
            "CONFIG_BOARD_TYPE_ESP32_S3N16R8_EMOJI=y",
        )
        if re.search(r"(?m)^CONFIG_BOARD_TYPE_ESP32_S3N16R8_EMOJI=y$", new):
            log("[OK] 板型开关已补为 y")

    write_text(sdk, new)
    log(f"[OK] 新值: CONFIG_OTA_URL=\"{ota_url}\"")


# ---------------------------------------------------------------- 5. 构建

def build():
    step(5, f"用 ESP-IDF 构建（target={IDF_TARGET}）")

    if not os.path.isfile(IDF_PROFILE):
        die(f"找不到 IDF 激活脚本：{IDF_PROFILE}\n"
            f"       先确认 v5.5.4 装好了（eim list）")

    # 有构建缓存就别再 set-target —— 那会把已有对象文件全清了重编（慢十几分钟），
    # 而且会把我们改好的 sdkconfig 重命名成 .old 再重生成。增量构建一样会
    # 检测到 sdkconfig 变化并重新配置。
    has_cache = os.path.isfile(os.path.join(SRC_DIR, "build", "CMakeCache.txt"))
    if has_cache:
        log("[*] 检测到已有构建缓存 -> 直接增量构建（不 set-target）")
        idf_cmd = "idf.py build"
    else:
        log("[*] 首次构建 -> set-target + build")
        idf_cmd = f"idf.py set-target {IDF_TARGET}; if ($?) {{ idf.py build }}"

    ps = (
        f". '{IDF_PROFILE}'; "
        f"Set-Location '{SRC_DIR}'; "
        f"{idf_cmd}; "
        f"exit $LASTEXITCODE"
    )
    r = run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]
    )
    if r.returncode != 0:
        die("构建失败，往上翻日志定位（常见：组件没装全、板型没选对）")

    bin_path = os.path.join(SRC_DIR, "build", "xiaozhi.bin")
    if not os.path.isfile(bin_path):
        die(f"构建声称成功但没找到 {bin_path}")
    size = os.path.getsize(bin_path)
    log("")
    log(f"[OK] 产物: {bin_path}")
    log(f"     大小: {size:,} 字节 ({size/1024/1024:.2f} MB)")
    if size > 4 * 1024 * 1024:
        log("     [WARN] 超过 4MB！ota_0/ota_1 分区只有 4MB，塞不进去")
    return bin_path


# ---------------------------------------------------------------- 5. 写 ota_1

def flash_ota1(bin_path, port):
    step(6, f"把固件写进 ota_1（端口 {port}，不碰 ota_0）")
    log("[!] 用 otatool 而不是 idf.py flash —— 后者会写 ota_0")

    ps = (
        f". '{IDF_PROFILE}'; "
        f"Set-Location '{SRC_DIR}'; "
        f"python \"$env:IDF_PATH/components/app_update/otatool.py\" "
        f"--port {port} write_ota_partition --name ota_1 --input '{bin_path}'; "
        f"exit $LASTEXITCODE"
    )
    r = run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps]
    )
    if r.returncode != 0:
        die("写 ota_1 失败。备用方案：esptool 直接写偏移 0x500000")
    log("[OK] 已写入 ota_1。此时**仍从 ota_0 启动**，设备行为不变。")
    log("     要切换到新版，需要改 otadata（见 docs/16 第三节）")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ota-url", default=DEFAULT_OTA_URL,
                    help=f"本机 OTA 地址（默认 {DEFAULT_OTA_URL}）")
    ap.add_argument("--flash", metavar="PORT",
                    help="编译后写进 ota_1，例如 --flash COM3")
    ap.add_argument("--source-existing", action="store_true",
                    help="跳过克隆（源码已就位时用）")
    args = ap.parse_args()

    log("Stage A · 生成『指向本机自建后台』的社区固件")
    log(f"项目根: {PROJECT}")
    log(f"源码:   {SRC_DIR}")
    log(f"OTA:    {args.ota_url}")

    if not args.source_existing:
        clone()
    else:
        if not os.path.isdir(SRC_DIR):
            die(f"--source-existing 但 {SRC_DIR} 不存在")

    patch_sdkconfig_defaults(args.ota_url)
    patch_existing_sdkconfig(args.ota_url)
    bin_path = build()

    if args.flash:
        flash_ota1(bin_path, args.flash)

    log("")
    log("=" * 66)
    log("完成。下一步：切分区 / 抓串口日志验证（见 docs/16 第三节）")
    log("=" * 66)


if __name__ == "__main__":
    main()
