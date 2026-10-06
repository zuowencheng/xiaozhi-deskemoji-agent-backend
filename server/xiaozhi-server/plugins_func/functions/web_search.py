import httpx
from config.logger import setup_logging
from plugins_func.register import (
    register_function,
    ToolType,
    ActionResponse,
    Action,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()

_DEFAULT_DESCRIPTION = (
    "联网搜索工具。当用户明确需要联网搜索问题时使用此工具。"
)

WEB_SEARCH_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": _DEFAULT_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或问题",
                }
            },
            "required": ["query"],
        },
    },
}


async def _search_metaso(api_key: str, query: str, max_results: int) -> str:
    """调用秘塔搜索API"""
    url = "https://metaso.cn/api/v1/search"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "q": query,
        "size": max_results,
        "stream": False,
        "scope": "webpage",
        "includeSummary": True,
        "includeRawContent": False,
        "conciseSnippet": False,
    }
    logger.bind(tag=TAG).debug(f"秘塔搜索请求 | URL: {url} | payload: {payload}")
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=3.0)) as client:
        response = await client.post(url, json=payload, headers=headers)
    data = response.json()
    logger.bind(tag=TAG).debug(f"秘塔搜索响应 | status: {response.status_code}")

    webpages = data.get("webpages", [])
    if not webpages:
        return "未找到相关搜索结果。"

    lines = ["【联网搜索结果】"]
    for i, item in enumerate(webpages, 1):
        title = item.get("title", "无标题")
        snippet = item.get("summary", "")
        date = item.get("date", "")
        lines.append(f"{i}. 标题：{title}")
        if date:
            lines.append(f"   日期：{date}")
        if snippet:
            lines.append(f"   摘要：{snippet}")

    return "\n".join(lines)


async def _search_tavily(api_key: str, query: str, max_results: int,
                         include_answer: bool = True,
                         search_depth: str = "basic") -> str:
    """调用 Tavily 搜索 API。

    ⚠️ 2026-10-03 修三个问题（实测 HTTP 401 / 结果里啥也没有）：
    1. **api_key 必须在 payload 里**。上游原来只放进 Authorization 头，
       Tavily 新版接口两个都要，缺 body 里的 key 直接 401。
    2. **search_depth 默认 basic**。上游写死 "advanced"：慢一倍、credit 翻倍，
       对"今天油价"这类问题完全没必要。
    3. **摘要不能注释掉**。上游把 results 的 title/content 全注释了，
       只返回 answer —— 一旦 Tavily 没给 answer（很多普通查询不给），
       拼出来就是「总结：」后面空白，LLM 只能回"我查不到"。
       现在两条都给：先 answer，再最多 3 条带 URL 的摘要（LLM 可以自己去核对）。
    """
    url = "https://api.tavily.com/search"
    headers = {"Content-Type": "application/json"}
    payload = {
        "api_key": api_key,          # ★ 必须在 body 里
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": include_answer,
    }
    logger.bind(tag=TAG).info(f"Tavily 搜索 | query={query} | depth={search_depth}")
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0, connect=8.0)) as client:
        response = await client.post(url, json=payload, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"Tavily HTTP {response.status_code}: {response.text[:180]}")
    data = response.json()

    results = data.get("results", []) or []
    answer = (data.get("answer") or "").strip()
    if not answer and not results:
        return "未找到相关搜索结果。"

    lines = ["【联网搜索结果】"]
    if answer:
        # ⚠️ Tavily 的 answer 经常是英文（它按 query 语言判，用户说中文也常回英文）。
        # 这里显式要求 LLM 用中文转述，否则小智会开始说英文。
        lines.append(f"总结（可能是英文，请用中文转述给用户）：{answer}")
    # ★ 摘要 + 来源链接：answer 缺失时这是唯一可用信息，LLM 可据此判断可信度
    for i, item in enumerate(results[:3], 1):
        title = (item.get("title") or "无标题").strip()
        content = (item.get("content") or "").strip()
        url_ = (item.get("url") or "").strip()
        seg = f"{i}. {title}"
        if content:
            seg += f"：{content[:300]}"
        if url_:
            seg += f"（来源：{url_}）"
        lines.append(seg)
    return "\n".join(lines)


async def _search_serply(api_key: str, query: str, max_results: int) -> str:
    """调用Serply搜索API"""
    url = "https://api.serply.io/v1/search"
    headers = {
        "X-Api-Key": api_key,
        "User-Agent": "xiaozhi-esp32-server",
    }
    # Serply单页最多返回10条，且返回条数可能略多于num，需要本地再截断一次
    params = {"q": query, "num": min(max_results, 10)}
    logger.bind(tag=TAG).debug(f"Serply搜索请求 | URL: {url} | params: {params}")
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=3.0)) as client:
        response = await client.get(url, params=params, headers=headers)
    data = response.json()
    logger.bind(tag=TAG).debug(f"Serply搜索响应 | status: {response.status_code}")

    if response.status_code != 200:
        detail = data.get("detail", "") if isinstance(data, dict) else ""
        logger.bind(tag=TAG).error(
            f"Serply搜索失败 | status: {response.status_code} | detail: {detail}"
        )
        return "联网搜索请求失败，请检查API Key是否正确。"

    results = data.get("results", [])
    if not results:
        return "未找到相关搜索结果。"

    lines = ["【联网搜索结果】"]
    for i, item in enumerate(results[:max_results], 1):
        title = item.get("title", "无标题")
        snippet = item.get("description", "")
        lines.append(f"{i}. 标题：{title}")
        if snippet:
            lines.append(f"   摘要：{snippet}")

    return "\n".join(lines)


@register_function("web_search", WEB_SEARCH_FUNCTION_DESC, ToolType.SYSTEM_CTL)
async def web_search(conn: "ConnectionHandler", query: str = None):
    logger.bind(tag=TAG).info(f"web_search 被调用 | query={query}")
    if not query:
        return ActionResponse(Action.REQLLM, "请提供搜索关键词。", None)

    web_search_config = conn.config.get("plugins", {}).get("web_search", {})
    max_results = int(web_search_config.get("max_results", 4))
    # 2026-10-03：走新的多引擎链路（firecrawl keyless 主力 + 自动降级 + 额度记账）
    if web_search_config.get("engines"):
        from core.providers.tts.web_search_multi import search_text, quota_alerts
        import asyncio as _aio
        text = await _aio.to_thread(search_text, query, web_search_config, max_results)
        # 额度快用完时顺带提醒（阈值 85%）
        hot = quota_alerts(0.85)
        if hot:
            text += ("\n【额度提醒】" + "、".join(
                f"{h['engine']} 已用 {h['used']}/{h['quota']}（{h['percent']}%）"
                for h in hot))
        return ActionResponse(Action.REQLLM, text, None)

    provider = web_search_config.get("provider", "").lower()
    logger.bind(tag=TAG).info(f"web_search 配置 | provider={provider} | max_results={max_results} | config_keys={list(web_search_config.keys())}")

    api_key = web_search_config.get("api_key", "")
    if not api_key or api_key.strip() in ("mk-xxx", "tvly-xxx", "your-key", ""):
        # ⚠️ 这条提示会被 LLM 读进上下文然后念给用户听，必须说人话：
        #    上游原文「联网搜索功能未配置API Key，请在配置文件中填写」——
        #    LLM 会当成"搜索坏了"，其实是你还没配 key。
        return ActionResponse(
            Action.REQLLM,
            "联网搜索还没有配置 API Key，暂时搜不了。需要在配置文件 "
            "data/.config.yaml 的 plugins.web_search.api_key 里填上 Tavily 的 key"
            "（tvly- 开头）。请转告主人去 Tavily 官网免费申请一个。",
            None,
        )

    try:
        if provider == "metaso":
            result_text = await _search_metaso(api_key, query, max_results)
        elif provider == "tavily":
            result_text = await _search_tavily(
                api_key, query, max_results,
                include_answer=bool(web_search_config.get("include_answer", True)),
                search_depth=str(web_search_config.get("search_depth", "basic")),
            )
        elif provider == "serply":
            result_text = await _search_serply(api_key, query, max_results)
        else:
            return ActionResponse(
                Action.REQLLM,
                f"联网搜索功能未配置或配置的搜索源无效（当前：{provider}），请检查配置。",
                None,
            )
        logger.bind(tag=TAG).info(f"搜索结果组装完成:\n{result_text}")
    except httpx.TimeoutException:
        logger.bind(tag=TAG).error("联网搜索请求超时")
        result_text = "联网搜索请求超时，请稍后重试。"
    except httpx.HTTPStatusError as e:
        logger.bind(tag=TAG).error(f"联网搜索请求失败: {e}")
        result_text = "联网搜索请求失败，请稍后重试。"
    except Exception as e:
        logger.bind(tag=TAG).error(f"联网搜索异常: {e}")
        result_text = "联网搜索出现异常，请稍后重试。"

    return ActionResponse(Action.REQLLM, result_text, None)
