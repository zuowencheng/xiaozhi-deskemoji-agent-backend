# -*- coding: utf-8 -*-
"""验证 memory_tools 插件：注册、展开、remember/recall/list/forget 全链路。"""
import os
import sys
from pathlib import Path

SRV = Path(r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\server\xiaozhi-server")
sys.path.insert(0, str(SRV))
os.chdir(SRV)

from plugins_func.loadplugins import auto_import_modules  # noqa: E402
from plugins_func.register import all_function_registry, module_func_map  # noqa: E402


def main():
    auto_import_modules("plugins_func.functions")
    print("=== module_func_map 里 memory_tools 展开 ===")
    print("memory_tools ->", module_func_map.get("memory_tools"))
    print()
    print("=== 四个工具是否注册 ===")
    for n in ("remember", "recall", "list_memories", "forget"):
        print(f"  {n}: {'OK' if n in all_function_registry else 'MISSING'}")
    print()

    from plugins_func.functions import memory_tools as mt

    print("=== remember（记一条，profile 类）===")
    r = mt.remember("主人喜欢简洁直接的说话方式，讨厌客套", "profile", "偏好,沟通")
    print("  action:", r.action, "| result:", r.result)
    print()

    print("=== recall（语义检索）===")
    r = mt.recall("主人喜欢什么说话风格", limit=3)
    print("  action:", r.action)
    print("  result:", (r.result or "")[:400])
    print()

    print("=== list_memories ===")
    r = mt.list_memories(limit=5)
    print("  result:", (r.result or "")[:400])
    print()

    print("=== 记忆库统计 ===")
    mem = mt._memory()
    if mem:
        import json
        print(json.dumps({**mem.stats(), "model": mem._model()}, ensure_ascii=False))
    else:
        print("  记忆模块不可用:", mt._MEM_ERR)
    print()

    print("=== recall 查不存在的东西（应如实说没查到）===")
    r = mt.recall("量子纠缠和螺蛳粉的关系", limit=3)
    print("  result:", (r.result or "")[:200])


if __name__ == "__main__":
    main()
