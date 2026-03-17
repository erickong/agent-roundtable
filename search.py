"""Tavily search integration for background research."""

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"


def get_tavily_api_key() -> Optional[str]:
    return os.environ.get("TAVILY_API_KEY", "").strip() or None


async def tavily_search(
    query: str,
    max_results: int = 10,
    search_depth: str = "advanced",
    topic: str = "general",
    api_key: Optional[str] = None,
) -> dict:
    """Search using Tavily API.

    Args:
        query: Search query string.
        max_results: Maximum number of results (default 10).
        search_depth: "basic" or "advanced" (default "advanced").
        topic: "general" or "news" (default "general").
        api_key: Tavily API key. Falls back to TAVILY_API_KEY env var.

    Returns:
        Dict with 'results' list and formatted 'summary' string.
    """
    key = api_key or get_tavily_api_key()
    if not key:
        raise ValueError("TAVILY_API_KEY not configured. Set it in .env")

    payload = {
        "api_key": key,
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "topic": topic,
        "include_answer": True,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(TAVILY_API_URL, json=payload)
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
