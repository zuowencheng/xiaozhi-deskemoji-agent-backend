# -*- coding: utf-8 -*-
"""一次性修复：loguru 日志占位符 %s/%d -> {}（loguru 只认 {}）"""
import io
import sys

SRV = r"C:\Users\zuowe\WorkBuddy\2026-10-02-01-36-40\xiaozhi-deskemoji-agent\server\xiaozhi-server"

TARGETS = {
    r"core\pc_agent.py": [
        (
            '            logger.error("[pc_agent] LLM 调用失败: %s", e)',
            '            logger.error(f"[pc_agent] LLM 调用失败: {type(e).__name__}: {e}")',
        ),
        (
            '            logger.info("[pc_agent] 完成（%d 步）：%s", steps, text[:80])',
            '            logger.info(f"[pc_agent] 完成（{steps} 步）：{text[:80]}")',
        ),
        (
            '            logger.info("[pc_agent] 步骤%d %s(%s) -> %s", steps, name,\n'
            '                        str(args)[:120], str(out)[:160].replace("\\n", " "))',
            '            logger.info(\n'
            '                f"[pc_agent] 步骤{steps} {name}({str(args)[:120]}) -> {str(out)[:160]}"\n'
            '            )',
        ),
        (
            '        logger.info("[pc_agent] 跳过 %s（状态=%s）", path.name, status)',
            '        logger.info(f"[pc_agent] 跳过 {path.name}（状态={status}）")',
        ),
        (
            '    logger.info("[pc_agent] 开始执行 %s：%s", tid, content[:80])',
            '    logger.info(f"[pc_agent] 开始执行 {tid}：{content[:80]}")',
        ),
        (
            '        logger.error("[pc_agent] 执行异常: %s", e)',
            '        logger.error(f"[pc_agent] 执行异常: {type(e).__name__}: {e}")',
        ),
        (
            '    logger.info("[pc_agent] %s 完成（%sms，%d步）：%s",\n'
            '                tid, out.get("ms"), out.get("steps"), result[:100])',
            '    logger.info(\n'
            '        f"[pc_agent] {tid} 完成（{out.get(\'ms\')}ms，{out.get(\'steps\')}步）：{result[:100]}"\n'
            '    )',
        ),
        (
            '        logger.error("[pc_agent] 移动到 done 失败: %s", e)',
            '        logger.error(f"[pc_agent] 移动到 done 失败: {type(e).__name__}: {e}")',
        ),
    ],
    r"core\providers\tools\unified_tool_manager.py": [
        (
            '            self.logger.info(\n'
            '                "已屏蔽 %d 个设备端定时/日程工具（改由服务端 set_reminder 主动播报）: %s",\n'
            '                len(excluded),\n'
            '                ", ".join(sorted(excluded)),\n'
            '            )',
            '            self.logger.info(\n'
            '                f"已屏蔽 {len(excluded)} 个设备端定时/日程工具（改由服务端 set_reminder 主动播报）: "\n'
            '                + ", ".join(sorted(excluded))\n'
            '            )',
        ),
    ],
}


def main():
    total = 0
    for rel, pairs in TARGETS.items():
        path = SRV + "\\" + rel
        with io.open(path, "r", encoding="utf-8") as f:
            text = f.read()
        for old, new in pairs:
            n = text.count(old)
            if n == 1:
                text = text.replace(old, new)
                total += 1
                print("OK   %s :: %s" % (rel, old.strip().splitlines()[0][:70]))
            elif n == 0:
                print("MISS %s :: %s" % (rel, old.strip().splitlines()[0][:70]))
            else:
                print("DUP(%d) %s :: %s" % (n, rel, old.strip().splitlines()[0][:70]))
        with io.open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    print("替换成功 %d 处" % total)


if __name__ == "__main__":
    main()
