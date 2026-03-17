"""Web search integration for background research.

Supports Tavily API and any Tavily-compatible search service (e.g. local news API).
Configure via SEARCH_API_URL env var; defaults to Tavily when TAVILY_API_KEY is set.
"""

import logging
import os
from typing import Optional

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


async def tavily_search(
    query: str,
    max_results: int = 10,
    search_depth: str = "advanced",
    topic: str = "general",
    days: int = 3,
    api_key: Optional[str] = None,
) -> dict:
    """Search using Tavily-compatible API.

    Args:
        query: Search query string.
        max_results: Maximum number of results (default 10).
        search_depth: "basic" or "advanced" (default "advanced").
        topic: "general" or "news" (default "general").
        days: Search within the last N days (default 3).
        api_key: Tavily API key. Falls back to TAVILY_API_KEY env var.

    Returns:
        Dict with 'results' list and formatted 'summary' string.
    """
    url = get_search_api_url()
    if not url:
        raise ValueError("No search backend configured. Set SEARCH_API_URL or TAVILY_API_KEY in .env")

    payload = {
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "topic": topic,
        "days": days,
        "include_answer": True,
    }

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

    # Format as readable background text
    lines = []
    if answer:
        lines.append(f"## 搜索摘要\n{answer}\n")
    lines.append(f"## 搜索结果 (共 {len(results)} 条)\n")
    for i, r in enumerate(results, 1):
        title = r.get("title", "无标题")
        url = r.get("url", "")
        content = r.get("content", "")
        lines.append(f"### {i}. {title}")
        if url:
            lines.append(f"来源: {url}")
        if content:
            lines.append(content)
        lines.append("")

    return {
        "results": results,
        "answer": answer,
        "summary": "\n".join(lines),
        "result_count": len(results),
    }


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
