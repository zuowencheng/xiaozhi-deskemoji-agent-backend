# -*- coding: utf-8 -*-
"""
把 active-notify 插件接进上游 app.py（幂等、可回滚）

改动只有两处：
  1. 导入     from core.notify_plugin import install_notify
  2. 安装     install_notify(config)      ← 放在 config = await load_config() 之后

用法：
    python tools/notify_patch_app.py            # 打补丁
    python tools/notify_patch_app.py --revert   # 从 .bak 还原
"""

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
APP_PY = os.path.join(PROJECT, "server", "xiaozhi-server", "app.py")
BAK = APP_PY + ".bak"

IMPORT_ANCHOR = "from core.websocket_server import WebSocketServer"
IMPORT_LINE = "from core.notify_plugin import install_notify"

INSTALL_ANCHOR = "    config = await load_config()"
INSTALL_LINE = "    install_notify(config)  # active-notify：主动播报接口（自研增量）"


def read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


def write(p, s):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--revert", action="store_true", help="从 .bak 还原")
    args = ap.parse_args()

    if not os.path.isfile(APP_PY):
        print(f"[FAIL] 找不到 {APP_PY}")
        return 2

    if args.revert:
        if not os.path.isfile(BAK):
            print(f"[FAIL] 没有备份 {BAK}，无法还原")
            return 2
        shutil.copy2(BAK, APP_PY)
        print(f"[OK] 已从 {BAK} 还原 app.py")
        return 0

    src = read(APP_PY)

    if "notify_plugin" in src:
        print("[SKIP] app.py 已经打过补丁，未做修改")
        return 0

    if IMPORT_ANCHOR not in src:
        print(f"[FAIL] 找不到导入锚点：{IMPORT_ANCHOR}")
        print("       上游可能改名了，请手动插入这两行：")
        print(f"         {IMPORT_LINE}")
        return 2
    if INSTALL_ANCHOR not in src:
        print(f"[FAIL] 找不到安装锚点：{INSTALL_ANCHOR}")
        print(f"       请手动在 main() 里加上：{INSTALL_LINE.strip()}")
        return 2

    if not os.path.isfile(BAK):
        shutil.copy2(APP_PY, BAK)
        print(f"[OK] 已备份 -> {BAK}")

    src = src.replace(
        IMPORT_ANCHOR, IMPORT_ANCHOR + "\n" + IMPORT_LINE, 1
    )
    src = src.replace(
        INSTALL_ANCHOR, INSTALL_ANCHOR + "\n" + INSTALL_LINE, 1
    )
    write(APP_PY, src)

    print("[OK] app.py 已打好补丁：")
    print(f"     + {IMPORT_LINE}")
    print(f"     + {INSTALL_LINE.strip()}")
    print("     重启服务后，日志里应出现『active-notify 插件已装载』")
    return 0


if __name__ == "__main__":
    sys.exit(main())
