"""Research skill — allows agents to use local news, web search, and page fetch.

Each agent may use this skill up to five times per round to:
- Support or verify their own arguments with facts
- Find evidence to critique others' arguments
- Gather recent local-news context and open-web fundamentals
"""

import logging
from typing import Optional

from search import (
    fetch_webpage_content,
    get_search_api_url,
    get_web_search_api_url,
    tavily_search,
    web_search,
)

logger = logging.getLogger(__name__)


def is_available() -> bool:
    """Check if any research backend is configured."""
    return get_search_api_url() is not None or get_web_search_api_url() is not None


async def execute(
    backend: str = "local",
    query: str = "",
    url: Optional[str] = None,
    category: Optional[str] = None,
    source: Optional[str] = None,
    days: int = 3,
    max_results: int = 20,
    search_depth: str = "advanced",
    include_domains: Optional[list[str]] = None,
    exclude_domains: Optional[list[str]] = None,
    max_chars: int = 12000,
) -> dict:
    """Run a research operation and return results.

    Returns:
        Dict with 'results', 'answer', 'summary', and 'result_count'.
    """
    normalized_backend = (backend or "local").strip().lower()

    if normalized_backend == "local":
        actual_query = "" if query.upper() == "ALL" else query
        return await tavily_search(
            query=actual_query,
            category=category,
            source=source,
            days=days,
            max_results=max_results,
            topic="news",
        )

    if normalized_backend == "web":
        return await web_search(
            query=query,
            max_results=max_results,
            search_depth=search_depth,
            topic="general",
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            days=days,
        )

    if normalized_backend == "fetch":
        target_url = (url or query).strip()
        return await fetch_webpage_content(target_url, max_chars=max_chars)

    raise ValueError(f"Unknown research backend: {backend}")


# Prompt instruction appended to expert prompts when search is enabled.
# Written in Chinese to match existing prompts; LANG_FOLLOW_INSTRUCTION
# ensures the LLM adapts to the user's language automatically.

SKILL_PROMPT = """

## 可用技能：研究工具
你拥有最多 5 次研究工具调用机会。研究工具分为三类：

1. 本地新闻数据库（backend="local"）
     适合：最近 1-3 天财经新闻、市场异动、政策消息、行业快讯。

2. 开放网页搜索（backend="web"）
     适合：东方财富、雪球、新浪财经、公司公告、券商研报、基本面资料、产业链分析。

3. 网页获取（backend="fetch"）
     适合：当你已经通过网页搜索找到一个高价值 URL，需要读取该页面正文时使用。

如果你需要查找最新资料来支持论点、验证事实、或反驳他人观点，请在你的 JSON 输出中额外添加一个 "search_query" 字段。
你可以返回字符串或 JSON 对象：

1. 本地新闻关键词（字符串，默认 backend=local）：
"search_query": "关税"

2. 本地新闻浏览（对象）：
"search_query": {
    "backend": "local",
    "query": "",
    "category": "china_finance",
    "days": 3,
    "max_results": 20
}

3. 开放网页搜索（对象）：
"search_query": {
    "backend": "web",
    "query": "鹏鼎控股 世运电路 东方财富 2026年3月",
    "include_domains": ["eastmoney.com", "sina.com.cn", "xueqiu.com"],
    "max_results": 8
}

4. 网页获取（对象）：
"search_query": {
    "backend": "fetch",
    "url": "https://caifuhao.eastmoney.com/news/..."
}

规则：
- 每轮最多调用 5 次研究工具
- 默认先做关键词搜索，不要一上来就用 ALL 或空 query 浏览本地新闻
- 本地新闻 query 只能是 1 个词；开放网页搜索 query 可以是自然语言短语
- 研究股票投资价值、公司公告、财务指标、券商评级、东方财富/雪球页面时，优先使用 backend="web" 或 backend="fetch"
- 只有在本地关键词没有结果，或者你需要市场全景时，才使用本地浏览模式（ALL 或 query=""）
- 如果已经拿到一个高价值网页 URL，不要继续泛搜，直接用 backend="fetch" 获取页面正文
- 如果浏览过某个 category 或抓取过某个 URL 仍没有新增信息，不要重复同一个参数
- 本地新闻默认返回 20 条，开放网页搜索默认建议 8 条
- 如果信息足够，就直接输出最终回答，不要再添加 search_query
- 如果不需要搜索，**不要**添加该字段
"""


SEARCH_REFINE_PROMPT = """
--- 第 {search_index}/{max_searches} 次搜索结果 ---
你之前请求搜索了「{query}」，以下是搜索到的信息：

{summary}

请基于以上搜索结果，结合你的专业判断和之前的分析，继续推进回答。
- 如果信息已经足够，请直接输出完整最终回答，不要再添加 search_query 字段。
- 如果还缺关键信息，你仍可继续添加新的 search_query。
- 剩余搜索次数：{remaining_searches}
- 不要重复已经搜索过的 query/category/source 组合。
"""


SEARCH_DUPLICATE_PROMPT = """
--- 搜索提醒 ---
你刚才重复请求了「{query}」，系统不会再次执行相同搜索。
如果还需要补充信息，请换一个新的 query/category/source；否则直接输出最终回答。
剩余搜索次数：{remaining_searches}
"""


SEARCH_KEYWORD_FIRST_PROMPT = """
--- 搜索提醒 ---
本轮请先做至少一次关键词搜索，不要一开始就使用 ALL 或空 query 浏览。
请先给出一个单词关键词；如果关键词没有结果，再改用浏览模式。
剩余搜索次数：{remaining_searches}
"""


SEARCH_ERROR_PROMPT = """
--- 搜索失败 ---
你请求的搜索「{query}」执行失败：{error}
如果还需要补充信息，请换一个新的 query/category/source；否则直接输出最终回答。
剩余搜索次数：{remaining_searches}
"""


SEARCH_FINAL_ONLY_PROMPT = """
你已经用完本轮的搜索次数。现在必须基于已有搜索结果直接输出最终回答，不要再添加 search_query 字段。
"""
