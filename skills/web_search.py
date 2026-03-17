"""Web search skill — allows agents to search for real-time information.

Each agent may use this skill at most once per round to:
- Support or verify their own arguments with facts
- Find evidence to critique others' arguments
- Gather background knowledge (news, reports, etc.)
"""

import logging
from typing import Optional

from search import tavily_search, get_search_api_url

logger = logging.getLogger(__name__)


def is_available() -> bool:
    """Check if a search backend is configured."""
    return get_search_api_url() is not None


async def execute(query: str = "", category: Optional[str] = None, max_results: int = 5) -> dict:
    """Run a web search and return results.

    Returns:
        Dict with 'results', 'answer', 'summary', and 'result_count'.
    """
    # Convert "ALL" to empty query to fetch all latest news
    actual_query = "" if query.upper() == "ALL" else query
    return await tavily_search(query=actual_query, category=category, max_results=max_results, topic="news")


# Prompt instruction appended to expert prompts when search is enabled.
# Written in Chinese to match existing prompts; LANG_FOLLOW_INSTRUCTION
# ensures the LLM adapts to the user's language automatically.

SKILL_PROMPT = """

## 可用技能：本地新闻数据库
你拥有一次查询本地新闻数据库的机会。该数据库包含全球及中国金融、科技、国际、军事等最新新闻。
如果你需要查找最新资料来支持论点、验证事实、或反驳他人观点，请在你的 JSON 输出中额外添加一个 "search_query" 字段。
支持更精确的参数（你可以返回一个字符串或JSON对象）：
1. 关键词查询（字符串）："search_query": "关税"
2. 高级查询（对象）：
"search_query": {
  "query": "关键词，只能包含1个词（例如：A股、港股、芯片）。如果想获取最新全部新闻，请使用空字符串 \\"\\"",
  "category": "可选，新闻分类过滤。例如：china_finance, finance, international, tech, defense, asia, europe"
}

规则：
- 每轮最多搜索一次
- query 只能是1个词（只支持单个关键词查询）
- 好的例子：「A股」「港股」「芯片」「关税」「新能源」「黄金」「半导体」
- 坏的例子（不要这样写）：「AI芯片市场」「原油价格走势」「全球半导体投资」
- 如果搜索不到结果，可以用 "ALL" 作为 query 来获取最新全部新闻
- 如果不需要搜索，**不要**添加该字段
- 搜索完成后系统会将结果返回给你，届时请基于搜索结果重新组织完整回答"""


SEARCH_REFINE_PROMPT = """
--- 搜索结果 ---
你之前请求搜索了「{query}」，以下是搜索到的信息：

{summary}

请基于以上搜索结果，结合你的专业判断和之前的分析，重新组织并输出你的完整最终回答。
注意：不要再添加 search_query 字段。
"""
