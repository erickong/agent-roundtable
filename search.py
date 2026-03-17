"""Search and fetch integration for local news and open-web research.

Supports:
- local Tavily-compatible news APIs via SEARCH_API_URL
- generic web search via Tavily (or WEB_SEARCH_API_URL)
- direct webpage fetching for deeper page-level research
"""

import logging
import os
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

TAVILY_API_URL = "https://api.tavily.com/search"
DEFAULT_FETCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


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


def get_web_search_api_url() -> Optional[str]:
    """Return the configured open-web search API URL."""
    url = os.environ.get("WEB_SEARCH_API_URL", "").strip()
    if url:
        return url
    if get_tavily_api_key():
        return TAVILY_API_URL
    return None


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


def parse_research_command(command: str) -> dict[str, Optional[str]]:
    """Parse moderator research commands across local news and open web.

    Supported commands:
    - local keyword: A股
    - local browse all: ALL / RECENT
    - local browse category: RECENT:china_finance
    - open web search: WEB:鹏鼎控股 世运电路 东方财富
    - webpage fetch: FETCH:https://finance.eastmoney.com/...
    """
    normalized = command.strip()
    upper = normalized.upper()

    if upper.startswith("WEB:"):
        query = normalized.split(":", 1)[1].strip()
        return {
            "backend": "web",
            "query": query,
            "category": None,
            "url": None,
            "command_key": f"WEB:{query}",
            "display": f"WEB:{query}",
        }

    if upper.startswith("FETCH:"):
        url = normalized.split(":", 1)[1].strip()
        return {
            "backend": "fetch",
            "query": None,
            "category": None,
            "url": url,
            "command_key": f"FETCH:{url}",
            "display": f"FETCH:{url}",
        }

    query, category, command_key = parse_local_news_command(normalized)
    return {
        "backend": "local",
        "query": query,
        "category": category,
        "url": None,
        "command_key": command_key,
        "display": command_key,
    }


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


class _HTMLTextExtractor(HTMLParser):
    """Minimal HTML-to-text extractor for fetched pages."""

    _BLOCK_TAGS = {
        "article", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
        "header", "footer", "li", "main", "p", "section", "table", "td", "th", "tr",
    }
    _IGNORE_TAGS = {"script", "style", "noscript", "svg", "iframe"}

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []
        self._ignore_depth = 0
        self._in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs):
        if tag in self._IGNORE_TAGS:
            self._ignore_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag in self._IGNORE_TAGS and self._ignore_depth:
            self._ignore_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str):
        if self._ignore_depth:
            return
        text = re.sub(r"\s+", " ", unescape(data)).strip()
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
        self._parts.append(text)

    def get_text(self) -> str:
        text = " ".join(self._parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()


def _extract_html_text(html: str) -> tuple[str, str]:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    title = parser.title.strip()
    text = parser.get_text()
    return title, text


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


def _format_fetch_response(url: str, title: str, content: str) -> dict:
    display_title = title or url
    summary = "\n".join([
        "## 网页获取结果",
        f"标题: {display_title}",
        f"来源: {url}",
        "",
        content,
    ])
    return {
        "results": [{"title": display_title, "url": url, "content": content}],
        "answer": "",
        "summary": summary,
        "result_count": 1,
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


async def web_search(
    query: str,
    max_results: int = 8,
    search_depth: str = "advanced",
    topic: str = "general",
    include_domains: Optional[list[str]] = None,
    exclude_domains: Optional[list[str]] = None,
    api_key: Optional[str] = None,
    days: int = 7,
) -> dict:
    """Search the open web via Tavily or another generic web-search backend."""
    url = get_web_search_api_url()
    if not url:
        raise ValueError("No open-web search backend configured. Set TAVILY_API_KEY or WEB_SEARCH_API_URL in .env")

    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("Web search requires a non-empty query")

    payload: dict[str, Any] = {
        "query": normalized_query,
        "max_results": max_results,
        "search_depth": search_depth,
        "topic": topic,
        "include_answer": True,
        "include_raw_content": True,
    }
    if topic == "news" and days > 0:
        payload["days"] = days
    if include_domains:
        payload["include_domains"] = include_domains
    if exclude_domains:
        payload["exclude_domains"] = exclude_domains

    key = api_key or get_tavily_api_key()
    if key:
        payload["api_key"] = key

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

    return _format_search_response(data.get("results", []), data.get("answer", ""))


async def fetch_webpage_content(url: str, max_chars: int = 12000) -> dict:
    """Fetch a webpage directly and extract readable text content."""
    target_url = url.strip()
    if not target_url:
        raise ValueError("Webpage fetch requires a URL")

    headers = {"User-Agent": DEFAULT_FETCH_USER_AGENT}
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
        resp = await client.get(target_url)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").lower()
        text = resp.text

    if "html" in content_type or "<html" in text[:500].lower():
        title, extracted = _extract_html_text(text)
    else:
        title = target_url
        extracted = text

    cleaned = re.sub(r"\n{3,}", "\n\n", extracted).strip()
    if max_chars > 0:
        cleaned = cleaned[:max_chars]
    return _format_fetch_response(target_url, title, cleaned)


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
