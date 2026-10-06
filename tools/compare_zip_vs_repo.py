#!/usr/bin/env python3
"""对比任意 zip 快照与本地仓库目录的逐文件差异。

用法:
    python tools/compare_zip_vs_repo.py <zip路径> <本地仓库目录> [zip内前缀]

输出: 仅 zip 有 / 内容不同 / 仅本地有 三类清单 + 板型清单
"""
import sys
import os
import zipfile
import hashlib

SKIP_PREFIX = ("main/assets/", "docs/", "build/")
SKIP_DIRS = {".git", "managed_components", "build", ".cache"}


def sha(b):
    return hashlib.sha256(b).hexdigest()[:12]


def main():
    zp = sys.argv[1]
    root = sys.argv[2]
    z = zipfile.ZipFile(zp)
    names = [n for n in z.namelist() if not n.endswith("/")]
    # 自动推断顶层前缀
    if len(sys.argv) > 3:
        prefix = sys.argv[3]
    else:
        tops = {n.split("/")[0] for n in names if "/" in n}
        prefix = (sorted(tops)[0] + "/") if len(tops) == 1 else ""

    added, changed, same = [], [], 0
    zip_rel = set()
    for n in names:
        rel = n[len(prefix):] if prefix and n.startswith(prefix) else n
        zip_rel.add(rel)
        if rel.startswith(SKIP_PREFIX):
            continue
        local = os.path.join(root, *rel.split("/"))
        data = z.read(n)
        if not os.path.exists(local):
            added.append((rel, len(data)))
        else:
            with open(local, "rb") as f:
                ld = f.read()
            if ld == data:
                same += 1
            elif ld.replace(b"\r\n", b"\n") == data.replace(b"\r\n", b"\n"):
                same += 1  # 仅行尾不同，视为一致
            else:
                changed.append((rel, len(ld), len(data)))

    local_all = set()
    for dp, dn, fn in os.walk(root):
        parts = set(dp.split(os.sep))
        if parts & SKIP_DIRS:
            continue
        for f in fn:
            rp = os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/")
            if rp.startswith(SKIP_PREFIX):
                continue
            local_all.add(rp)
    removed = sorted(r for r in local_all if r not in zip_rel)

    print("zip前缀:", prefix or "(无)")
    print("完全相同:", same)
    print()
    print("=== 仅 zip 有 (%d) ===" % len(added))
    for r, s in sorted(added):
        print("  + %-70s %d" % (r, s))
    print()
    print("=== 内容不同 (%d) ===" % len(changed))
    for r, a, b in sorted(changed):
        print("  ~ %-70s %d -> %d" % (r, a, b))
    print()
    print("=== 仅本地有 (%d) ===" % len(removed))
    for r in removed[:80]:
        print("  -", r)


if __name__ == "__main__":
    main()
