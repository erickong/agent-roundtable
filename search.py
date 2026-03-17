"""Web search integration for background research.

Supports Tavily API and any Tavily-compatible search service (e.g. local news API).
Configure via SEARCH_API_URL env var; defaults to Tavily when TAVILY_API_KEY is set.
"""

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"


def get_search_api_url() -> Optional[str]:
    """Return the configured search API URL, or None if no search backend is available."""
    url = os.environ.get("SEARCH_API_URL", "").strip()
    if url:
        return url
    # Fall back to Tavily if API key is set
    if get_tavily_api_key():
        return TAVILY_API_URL
    return None


def get_tavily_api_key() -> Optional[str]:
    return os.environ.get("TAVILY_API_KEY", "").strip() or None


def parse_local_news_command(command: str) -> tuple[str, Optional[str], str]:
    """Parse a moderator browse/search command for the local news API.

    Supported commands:
    - one-word keyword, e.g. "A股"
    - ALL or RECENT
    - RECENT:<category>, e.g. RECENT:china_finance
    """
    normalized = command.strip()
    upper = normalized.upper()

    if upper in {"ALL", "RECENT"}:
        return "", None, "ALL"

    if upper.startswith("RECENT:"):
        category = normalized.split(":", 1)[1].strip().lower()
        return "", category or None, f"RECENT:{category}" if category else "ALL"

    return normalized, None, normalized


def _is_local_news_api(url: str) -> bool:
    return "192.168.3.89:4001" in url


def _get_recent_api_url(url: str) -> str:
    if url.endswith("/v1/search"):
        return url[:-len("/v1/search")] + "/recent"
    if url.endswith("/search"):
        return url[:-len("/search")] + "/recent"
    return url.rstrip("/") + "/recent"


def _normalize_recent_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title", "无标题"),
        "url": item.get("link", ""),
        "content": item.get("summary", ""),
        "score": item.get("score", 0.0),
        "raw_content": item.get("content"),
        "published": item.get("published"),
        "source": item.get("source"),
        "category": item.get("category"),
    }


def _infer_local_news_category(query: str) -> Optional[str]:
    normalized_query = query.strip().lower()
    if not normalized_query:
        return None

    china_finance_terms = {
        "a股", "港股", "沪深", "上证", "深证", "恒生", "创业板", "科创板",
        "券商", "基金", "投资", "政策", "并购", "ipo", "财联社",
    }
    tech_terms = {"芯片", "半导体", "ai", "人工智能", "算力", "英伟达", "科技"}
    energy_terms = {"原油", "石油", "黄金", "煤炭", "新能源", "光伏", "锂电"}

    if normalized_query in china_finance_terms:
        return "china_finance"
    if normalized_query in tech_terms:
        return "tech"
    if normalized_query in energy_terms:
        return "finance"
    return None


def _format_search_response(results: list[dict[str, Any]], answer: str = "") -> dict:
    lines = []
    if answer:
        lines.append(f"## 搜索摘要\n{answer}\n")
    lines.append(f"## 搜索结果 (共 {len(results)} 条)\n")
    for i, result in enumerate(results, 1):
        title = result.get("title", "无标题")
        result_url = result.get("url", "")
        content = result.get("content", "")
        lines.append(f"### {i}. {title}")
        if result_url:
            lines.append(f"来源: {result_url}")
        if content:
            lines.append(content)
        lines.append("")

    return {
        "results": results,
        "answer": answer,
        "summary": "\n".join(lines),
        "result_count": len(results),
    }


async def browse_recent_news(
    max_results: int = 20,
    category: Optional[str] = None,
    source: Optional[str] = None,
    days: int = 3,
) -> dict:
    """Browse latest news from the local News API via /recent."""
    url = get_search_api_url()
    if not url:
        raise ValueError("No search backend configured. Set SEARCH_API_URL or TAVILY_API_KEY in .env")
    if not _is_local_news_api(url):
        raise ValueError("Recent-news browse mode is only supported by the local News API")

    params: dict[str, Any] = {"limit": max_results}
    if category:
        params["category"] = category
    if source:
        params["source"] = source
    if days > 0:
        params["hours"] = days * 24

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(_get_recent_api_url(url), params=params)
        resp.raise_for_status()
        data = resp.json()

    results = [_normalize_recent_result(item) for item in data.get("results", [])]
    return _format_search_response(results)


async def tavily_search(
    query: str,
    max_results: int = 10,
    search_depth: str = "advanced",
    topic: str = "general",
    days: int = 3,
    api_key: Optional[str] = None,
    category: Optional[str] = None,
    source: Optional[str] = None,
) -> dict:
    """Search using Tavily-compatible API.

    Args:
        query: Search query string.
        max_results: Maximum number of results (default 10).
        search_depth: "basic" or "advanced" (default "advanced").
        topic: "general" or "news" (default "general").
        days: Search within the last N days (default 3).
        api_key: Tavily API key. Falls back to TAVILY_API_KEY env var.
        category: News category filter (e.g. china_finance, tech).
        source: News source filter.

    Returns:
        Dict with 'results' list and formatted 'summary' string.
    """
    url = get_search_api_url()
    if not url:
        raise ValueError("No search backend configured. Set SEARCH_API_URL or TAVILY_API_KEY in .env")

    normalized_query = query.strip()
    if _is_local_news_api(url) and not normalized_query:
        return await browse_recent_news(
            max_results=max_results,
            category=category,
            source=source,
            days=days,
        )

    payload = {
        "query": normalized_query,
        "max_results": max_results,
        "search_depth": search_depth,
        "topic": topic,
        "days": days,
        "include_answer": True,
    }
    
    if category:
        payload["category"] = category
    if source:
        payload["source"] = source

    # Include api_key only for Tavily (or when explicitly provided)
    key = api_key or get_tavily_api_key()
    if key:
        payload["api_key"] = key

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    results = data.get("results", [])
    answer = data.get("answer", "")

    if _is_local_news_api(url) and topic == "news" and not results:
        fallback_category = category or _infer_local_news_category(normalized_query)
        fallback_data = await browse_recent_news(
            max_results=max_results,
            category=fallback_category,
            source=source,
            days=days,
        )
        category_label = fallback_category or "all"
        fallback_note = (
            f"## 关键词未命中，已切换到最新新闻浏览\n"
            f"原关键词: {normalized_query}\n"
            f"浏览分类: {category_label}\n\n"
        )
        fallback_data["summary"] = fallback_note + fallback_data["summary"]
        fallback_data["answer"] = answer
        return fallback_data

    return _format_search_response(results, answer)


async def search_for_topic(topic: str, api_key: Optional[str] = None) -> str:
    """Convenience function: search for a topic and return a formatted background string."""
    try:
        data = await tavily_search(
            query=topic,
            max_results=10,
            search_depth="advanced",
            topic="news",
            api_key=api_key,
        )
        return data["summary"]
    except Exception as e:
        logger.error("Tavily search failed: %s", e)
        raise
