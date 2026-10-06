# -*- coding: utf-8 -*-
"""
修复 Windows 批处理脚本的行尾问题。

背景：用 Unix 风格（LF）行尾保存的 .cmd/.bat，在 cmd.exe 下会被按错误的
方式切分，表现为「双击窗口一闪就没」或大量 "不是内部或外部命令"。

本脚本：扫描项目下所有 .cmd/.bat，把 LF-only 的转换为 CRLF，
并保留 .bak 备份。编码保持 UTF-8（无 BOM），配合脚本内的 chcp 65001 使用。

用法：
    python tools/fix_cmd_encoding.py            # 扫描 + 修复
    python tools/fix_cmd_encoding.py --check    # 只扫描，不改动
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def scan(path: Path):
    raw = path.read_bytes()
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    return {
        "crlf": crlf,
        "lf_only": lf,
        "bom": raw[:3] == b"\xef\xbb\xbf",
        "ok": lf == 0,
    }


def fix(path: Path):
    raw = path.read_bytes()
    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        backup.write_bytes(raw)
    # 先统一成 LF，再整体替换为 CRLF，避免出现 \r\r\n
    text = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    path.write_bytes(text.replace(b"\n", b"\r\n"))


def main():
    check_only = "--check" in sys.argv
    targets = sorted(
        p for p in ROOT.rglob("*")
        if p.suffix.lower() in (".cmd", ".bat") and ".bak" not in p.suffixes
    )

    print(f"扫描目录: {ROOT}")
    print(f"找到 {len(targets)} 个批处理文件\n")
    print(f"{'文件':<46} {'状态':<12} {'CRLF':>6} {'LF':>6}")
    print("-" * 76)

    bad = []
    for p in targets:
        info = scan(p)
        rel = str(p.relative_to(ROOT))
        state = "OK" if info["ok"] else "LF-only(X)"
        if info["bom"]:
            state += " BOM"
        print(f"{rel:<46} {state:<12} {info['crlf']:>6} {info['lf_only']:>6}")
        if not info["ok"]:
            bad.append(p)

    print("-" * 76)
    if not bad:
        print("全部正常，无需修复。")
        return

    print(f"\n需要修复: {len(bad)} 个")
    for p in bad:
        print("   -", p.relative_to(ROOT))

    if check_only:
        print("\n(--check 模式，未做改动)")
        return

    for p in bad:
        fix(p)
        print(f"[已修复] {p.relative_to(ROOT)}  (备份 -> {p.name}.bak)")

    print("\n重新校验:")
    for p in bad:
        info = scan(p)
        print(f"   {'OK' if info['ok'] else '仍异常'}  {p.relative_to(ROOT)}  CRLF={info['crlf']} LF={info['lf_only']}")


if __name__ == "__main__":
    main()
