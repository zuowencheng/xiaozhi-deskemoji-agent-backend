# -*- coding: utf-8 -*-
"""快速增量重编（复用 stage_a_build 的环境清理与 IDF 激活逻辑）。

用法:
    python tools/_rebuild_quick.py
只做 idf.py build，不改 sdkconfig、不刷机。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stage_a_build as S  # noqa: E402

if __name__ == "__main__":
    S.build()
