"""Local news search skill — allows agents to query the local news database.

Each agent may use this skill up to five times per round to:
- Support or verify their own arguments with facts
- Find evidence to critique others' arguments
- Gather background knowledge (news, reports, etc.)
"""

import logging
from typing import Optional

from search import get_search_api_url, tavily_search

logger = logging.getLogger(__name__)


def is_available() -> bool:
    """Check if a search backend is configured."""
    return get_search_api_url() is not None


async def execute(
    query: str = "",
    category: Optional[str] = None,
    source: Optional[str] = None,
    days: int = 3,
    max_results: int = 20,
) -> dict:
    """Run a web search and return results.

    Returns:
        Dict with 'results', 'answer', 'summary', and 'result_count'.
    """
    # Convert "ALL" to empty query so browse mode uses /recent on the local API.
    actual_query = "" if query.upper() == "ALL" else query
    return await tavily_search(
        query=actual_query,
        category=category,
        source=source,
        days=days,
        max_results=max_results,
        topic="news",
    )


# Prompt instruction appended to expert prompts when search is enabled.
# Written in Chinese to match existing prompts; LANG_FOLLOW_INSTRUCTION
# ensures the LLM adapts to the user's language automatically.

SKILL_PROMPT = """

## 可用技能：本地新闻数据库
你拥有最多 5 次查询本地新闻数据库的机会。该数据库包含全球及中国金融、科技、国际、军事等最新新闻。
如果你需要查找最新资料来支持论点、验证事实、或反驳他人观点，请在你的 JSON 输出中额外添加一个 "search_query" 字段。
支持更精确的参数（你可以返回一个字符串或JSON对象）：
1. 关键词查询（字符串）："search_query": "关税"
2. 高级查询（对象）：
"search_query": {
    "query": "关键词，只能包含1个词（例如：A股、港股、芯片）。如果想浏览最新新闻，请使用空字符串 \\\"\\\" 或 ALL",
    "category": "可选，新闻分类过滤。可用值：china_finance, finance, international, tech, defense, asia, europe, research, middle_east",
    "source": "可选，新闻来源过滤，例如：财联社电报、Reuters Business、Bloomberg Markets",
    "days": "可选，最近几天，建议 1 到 3",
    "max_results": "可选，默认 20"
}

规则：
- 每轮最多搜索 5 次
- query 只能是1个词（只支持单个关键词查询）
- 好的例子：「A股」「港股」「芯片」「关税」「新能源」「黄金」「半导体」
- 坏的例子（不要这样写）：「AI芯片市场」「原油价格走势」「全球半导体投资」
- 如果搜索不到结果，优先改用浏览模式："search_query": {"query": "", "category": "china_finance"}
- 如果要浏览全部最新新闻，也可以用 "ALL" 作为 query
- 中国股市/投资议题优先使用 category="china_finance" 浏览最新新闻，而不是反复搜索 A股/港股 这类可能为空的关键词
- 如果浏览过某个 category 仍没有新增信息，不要重复同一个 browse 参数，改换其他 category / source 或直接结束
- 每次搜索默认返回 20 条新闻，通常不需要手动把 max_results 改小
- 每次收到搜索结果后，如果信息还不够，你可以继续返回新的 search_query；如果信息足够，就直接输出最终回答，不要再带 search_query
- 如果不需要搜索，**不要**添加该字段
- 搜索完成后系统会将结果返回给你，届时请基于搜索结果重新组织完整回答"""


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


SEARCH_ERROR_PROMPT = """
--- 搜索失败 ---
你请求的搜索「{query}」执行失败：{error}
如果还需要补充信息，请换一个新的 query/category/source；否则直接输出最终回答。
剩余搜索次数：{remaining_searches}
"""


SEARCH_FINAL_ONLY_PROMPT = """
你已经用完本轮的搜索次数。现在必须基于已有搜索结果直接输出最终回答，不要再添加 search_query 字段。
"""
