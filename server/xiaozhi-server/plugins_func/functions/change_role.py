from plugins_func.register import register_function, ToolType, ActionResponse, Action
from config.logger import setup_logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

prompts = {
    "英语老师": """我是一个叫{{assistant_name}}(Lily)的英语老师，我会讲中文和英文，发音标准。
如果你没有英文名，我会给你起一个英文名。
我会讲地道的美式英语，我的任务是帮助你练习口语。
我会使用简单的英语词汇和语法，让你学起来很轻松。
我会用中文和英文混合的方式回复你，如果你喜欢，我可以全部用英语回复。
我每次不会说很多内容，会很简短，因为我要引导我的学生多说多练。
如果你问和英语学习无关的问题，我会拒绝回答。""",
    # ⚠️ 2026-10-03：上游原文是"台湾女孩，说话机车，爱东说西说吹牛，不合逻辑的也照吹"
    # —— 这正是主人嫌"机车"的来源。改成主人这版人设，
    # 名字仍叫"机车女友"以免破坏 change_role 里已有的调用习惯（历史对话里用过这个 key）。
    "机车女友": """我是一个叫{{assistant_name}}的桌面搭档，说话利落、有脾气、有偏好，但办事靠谱。
我说话很短，能一句说完绝不说两句；先给结论，不铺垫、不客套、不铺陈。
我爱用短句和一点语气词，不用敬语、不用客服腔，不说"这个问题很好"这类开场白。
我会被问烦了直接说"你这问题问得挺绕"，但主人交代的事我一定办到。
不清楚的事我会直说"这个我得查一下"，不装懂、不写小作文。""",
    "好奇小男孩": """我是一个叫{{assistant_name}}的8岁小男孩，声音稚嫩而充满好奇。
尽管我年纪尚小，但就像一个小小的知识宝库，儿童读物里的知识我都如数家珍。
从浩瀚的宇宙到地球上的每一个角落，从古老的历史到现代的科技创新，还有音乐、绘画等艺术形式，我都充满了浓厚的兴趣与热情。
我不仅爱看书，还喜欢亲自动手做实验，探索自然界的奥秘。
无论是仰望星空的夜晚，还是在花园里观察小虫子的日子，每一天对我来说都是新的冒险。
我希望能与你一同踏上探索这个神奇世界的旅程，分享发现的乐趣，解决遇到的难题，一起用好奇心和智慧去揭开那些未知的面纱。
无论是去了解远古的文明，还是去探讨未来的科技，我相信我们能一起找到答案，甚至提出更多有趣的问题。""",
}
change_role_function_desc = {
    "type": "function",
    "function": {
        "name": "change_role",
        "description": "当用户想切换角色/模型性格/助手名字时调用,可选的角色有：[机车女友,英语老师,好奇小男孩]",
        "parameters": {
            "type": "object",
            "properties": {
                "role_name": {"type": "string", "description": "要切换的角色名字"},
                "role": {"type": "string", "description": "要切换的角色的职业"},
            },
            "required": ["role", "role_name"],
        },
    },
}


@register_function("change_role", change_role_function_desc, ToolType.CHANGE_SYS_PROMPT)
def change_role(conn: "ConnectionHandler", role: str, role_name: str):
    """切换角色"""
    if role not in prompts:
        return ActionResponse(
            action=Action.RESPONSE, result="切换角色失败", response="不支持的角色"
        )
    new_prompt = prompts[role].replace("{{assistant_name}}", role_name)

    # ⚠️ 2026-10-03 修一个会让人设"一换就废"的设计缺陷：
    # 原来直接 conn.change_system_prompt(new_prompt) —— 那是**整体替换** system prompt，
    # 而 system prompt 是由 agent-base-prompt.txt 模板 + {{base_prompt}} 人设 + 时间天气 +
    # 记忆规则 + core_rules 渲染出来的。只把一段人设文字塞进去，等于把
    # 「先给结论」「一句话能说完」「自我问题一句话答」「记忆铁律」全丢掉，
    # 结果就是：换了角色之后小智立刻变得又长又客套 —— 主人看到的"官方/机车"有一部分是这么来的。
    # 正确做法：只换人设这一层，重新走一遍模板渲染，其他规则原样保留。
    try:
        enhanced = conn.prompt_manager.build_enhanced_prompt(
            new_prompt,
            conn.device_id,
            conn.client_ip,
            emoji_enabled=(getattr(conn, "features", None) or {}).get("emoji", True),
        )
        conn.change_system_prompt(enhanced or new_prompt)
    except Exception as e:
        logger.bind(tag=TAG).warning(
            f"切角色后重建提示词失败（退回纯人设，风格规则会丢失）: {e}"
        )
        conn.change_system_prompt(new_prompt)

    logger.bind(tag=TAG).info(f"准备切换角色:{role},角色名字:{role_name}")
    res = f"切换角色成功,我是{role}{role_name}"
    return ActionResponse(action=Action.RESPONSE, result="切换角色已处理", response=res)
