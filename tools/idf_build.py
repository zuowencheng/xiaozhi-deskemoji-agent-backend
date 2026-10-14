# -*- coding: utf-8 -*-
"""
在 Git Bash 下绕过 idf.py 对 MSYS/MinGW 的「软拒绝」。

现象
----
ESP-IDF 的 tools/idf.py 在 `__main__` 里是这样的结构：

    if 'MSYSTEM' in os.environ:
        print_warning('MSys/Mingw is no longer supported ...')
    elif os.name == 'posix' and not _valid_unicode_config():
        ...
    else:
        main()

注意第一个分支**既不调用 main()、也不报错、退出码还是 0**。
Git Bash 恒定带 MSYSTEM=MINGW64，于是每次 `idf.py build` 都只是打印一句
警告后安静退出 —— 看起来 EXIT=0「编译成功」，实际一个文件都没编，
产物还是旧固件。这个坑非常隐蔽，排查时务必先确认 bin 时间戳。

做法
----
在 Python 进程内把 MSYSTEM/OSTYPE 摘掉，再 runpy 执行真正的 idf.py，
同时把 IDF 工具链（cmake/ninja/xtensa/riscv/ulp）拼进 PATH。

用法
----
    cd firmware/community
    python <本脚本> build          # 编译
    python <本脚本> -p COM4 flash  # 刷机
"""
import glob
import os
import runpy
import sys

IDF_PATH = r"C:\Espressif\v5.5.4\esp-idf"
# ⚠️ 是 C:\Espressif\tools 而不是 C:\Espressif。写成后者会报
#    "espidf.constraints.v5.5.txt doesn't exist"（那文件躺在 tools 下）。
TOOLS = r"C:\Espressif\tools"

# 关键：摘掉 MSYS 标识，否则 idf.py 走「只警告不干活」的分支
for _k in ("MSYSTEM", "OSTYPE"):
    os.environ.pop(_k, None)

# 下面这组变量照抄官方 PowerShell profile（Microsoft.v5.5.4.PowerShell_profile.ps1）。
# 少一个都会卡在不同阶段：IDF_TOOLS_PATH 错了 → 找不到 constraints 文件；
# IDF_PYTHON_ENV_PATH 错了 → "python.exe doesn't exist"。
os.environ["IDF_PATH"] = IDF_PATH
os.environ["IDF_TOOLS_PATH"] = TOOLS
os.environ["IDF_PYTHON_ENV_PATH"] = r"C:\Espressif\tools\python\v5.5.4\venv"
os.environ["IDF_COMPONENT_LOCAL_STORAGE_URL"] = r"file://C:\Espressif\tools"
os.environ["ESP_ROM_ELF_DIR"] = r"C:\Espressif\tools\esp-rom-elfs\20241011/"
os.environ["OPENOCD_SCRIPTS"] = (
    r"C:\Espressif\tools\openocd-esp32\v0.12.0-esp32-20251215"
    r"\openocd-esp32\share\openocd\scripts"
)
os.environ["IDF_CCACHE_ENABLE"] = "1"
os.environ.setdefault("ESP_IDF_VERSION", "5.5")
os.environ.setdefault("IDF_VERSION", "5.5.4")

# 与 profile 中 $env:PATH 一致（顺序照抄，工具链要盖在系统 PATH 之前）
_IDF_PATHS = [
    r"C:\Espressif\tools\ccache\4.12.1\ccache-4.12.1-windows-x86_64",
    r"C:\Espressif\tools\cmake\3.30.2\bin",
    r"C:\Espressif\tools\dfu-util\0.11\dfu-util-0.11-win64",
    r"C:\Espressif\tools\esp-clang\esp-19.1.2_20250312\esp-clang\bin",
    r"C:\Espressif\tools\esp-rom-elfs\20241011",
    r"C:\Espressif\tools\esp32ulp-elf\2.38_20240113\esp32ulp-elf\bin",
    r"C:\Espressif\tools\esp32ulp-elf\2.38_20240113\esp32ulp-elf\esp32ulp-elf\bin",
    r"C:\Espressif\tools\idf-exe\1.0.3",
    r"C:\Espressif\tools\ninja\1.12.1",
    r"C:\Espressif\tools\openocd-esp32\v0.12.0-esp32-20251215\openocd-esp32\bin",
    r"C:\Espressif\tools\riscv32-esp-elf\esp-14.2.0_20260121\riscv32-esp-elf\bin",
    r"C:\Espressif\tools\riscv32-esp-elf\esp-14.2.0_20260121"
    r"\riscv32-esp-elf\riscv32-esp-elf\bin",
    r"C:\Espressif\tools\xtensa-esp-elf-gdb\16.3_20250913\xtensa-esp-elf-gdb\bin",
    r"C:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20260121\xtensa-esp-elf\bin",
    r"C:\Espressif\tools\xtensa-esp-elf\esp-14.2.0_20260121"
    r"\xtensa-esp-elf\xtensa-esp-elf\bin",
    r"C:\Espressif\tools\python\v5.5.4\venv\Scripts",
]
os.environ["PATH"] = os.pathsep.join(_IDF_PATHS) + os.pathsep + os.environ.get("PATH", "")

if not sys.argv[1:]:
    sys.argv.append("build")

_target = os.path.join(IDF_PATH, "tools", "idf.py")
# 直接 `python idf.py` 时解释器会把脚本所在目录塞进 sys.path[0]，
# runpy 不会做这件事 —— 不加这行就会 ModuleNotFoundError: python_version_checker。
sys.path.insert(0, os.path.join(IDF_PATH, "tools"))
sys.argv[0] = _target
runpy.run_path(_target, run_name="__main__")
