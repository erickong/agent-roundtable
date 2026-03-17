import pytest

import search


class FakeResponse:
    def __init__(self, data=None, status_code=200, text="", headers=None):
        self._data = data
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


class FakeAsyncClient:
    def __init__(self, *, get_response=None, post_response=None):
        self.get_response = get_response
        self.post_response = post_response

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None, **kwargs):
        return self.get_response(url, params, **kwargs)

    async def post(self, url, json=None, **kwargs):
        return self.post_response(url, json, **kwargs)


@pytest.mark.anyio
async def test_tavily_search_uses_recent_endpoint_for_empty_query(monkeypatch):
    monkeypatch.setattr(search, "get_search_api_url", lambda: "http://192.168.3.89:4001/v1/search")

    def build_client(*args, **kwargs):
        return FakeAsyncClient(
            get_response=lambda url, params: FakeResponse(
                {
                    "total": 1,
                    "results": [
                        {
                            "title": "Latest news",
                            "link": "https://example.com/news",
                            "summary": "Recent story",
                            "published": "2026-03-17T00:00:00Z",
                            "source": "Test Source",
                            "category": "china_finance",
                        }
                    ],
                }
            ),
            post_response=lambda url, json: (_ for _ in ()).throw(AssertionError("POST should not be used for empty query")),
        )

    monkeypatch.setattr(search.httpx, "AsyncClient", build_client)

    result = await search.tavily_search(query="", max_results=5, topic="news")

    assert result["result_count"] == 1
    assert "Latest news" in result["summary"]


@pytest.mark.anyio
async def test_tavily_search_returns_zero_results_when_local_search_has_no_hits(monkeypatch):
    monkeypatch.setattr(search, "get_search_api_url", lambda: "http://192.168.3.89:4001/v1/search")

    def build_client(*args, **kwargs):
        return FakeAsyncClient(
            get_response=lambda url, params: (_ for _ in ()).throw(AssertionError("GET should not be used for keyword misses")),
            post_response=lambda url, json: FakeResponse(
                {
                    "query": "A股",
                    "answer": None,
                    "results": [],
                    "response_time": 0.01,
                }
            ),
        )

    monkeypatch.setattr(search.httpx, "AsyncClient", build_client)

    result = await search.tavily_search(query="A股", max_results=5, topic="news")

    assert result["result_count"] == 0
    assert "搜索结果 (共 0 条)" in result["summary"]


def test_parse_local_news_command_supports_recent_modes():
    assert search.parse_local_news_command("ALL") == ("", None, "ALL")
    assert search.parse_local_news_command("RECENT") == ("", None, "ALL")
    assert search.parse_local_news_command("RECENT:china_finance") == (
        "",
        "china_finance",
        "RECENT:china_finance",
    )
    assert search.parse_local_news_command("A股") == ("A股", None, "A股")


def test_parse_research_command_supports_web_and_fetch_modes():
    assert search.parse_research_command("WEB:鹏鼎控股 世运电路 东方财富") == {
        "backend": "web",
        "query": "鹏鼎控股 世运电路 东方财富",
        "category": None,
        "url": None,
        "command_key": "WEB:鹏鼎控股 世运电路 东方财富",
        "display": "WEB:鹏鼎控股 世运电路 东方财富",
    }
    assert search.parse_research_command("FETCH:https://example.com/report") == {
        "backend": "fetch",
        "query": None,
        "category": None,
        "url": "https://example.com/report",
        "command_key": "FETCH:https://example.com/report",
        "display": "FETCH:https://example.com/report",
    }


@pytest.mark.anyio
async def test_web_search_uses_open_web_backend(monkeypatch):
    monkeypatch.setattr(search, "get_web_search_api_url", lambda: "https://api.tavily.com/search")
    monkeypatch.setattr(search, "get_tavily_api_key", lambda: "test-key")

    def build_client(*args, **kwargs):
        return FakeAsyncClient(
            post_response=lambda url, json, **kw: FakeResponse(
                {
                    "query": json["query"],
                    "answer": "answer",
                    "results": [
                        {
                            "title": "Eastmoney result",
                            "url": "https://example.com/eastmoney",
                            "content": "content",
                        }
                    ],
                    "response_time": 0.01,
                }
            ),
            get_response=lambda url, params, **kw: (_ for _ in ()).throw(AssertionError("GET should not be used for web search")),
        )

    monkeypatch.setattr(search.httpx, "AsyncClient", build_client)

    result = await search.web_search(query="鹏鼎控股 世运电路 东方财富", max_results=3)

    assert result["result_count"] == 1
    assert "Eastmoney result" in result["summary"]


@pytest.mark.anyio
async def test_fetch_webpage_content_extracts_html_text(monkeypatch):
    html = """
    <html>
      <head><title>测试页面</title></head>
      <body>
        <article><h1>标题</h1><p>第一段。</p><p>第二段。</p></article>
        <script>ignored()</script>
      </body>
    </html>
    """

    def build_client(*args, **kwargs):
        return FakeAsyncClient(
            get_response=lambda url, params, **kw: FakeResponse(
                text=html,
                headers={"content-type": "text/html; charset=utf-8"},
            ),
            post_response=lambda url, json, **kw: (_ for _ in ()).throw(AssertionError("POST should not be used for webpage fetch")),
        )

    monkeypatch.setattr(search.httpx, "AsyncClient", build_client)

    result = await search.fetch_webpage_content("https://example.com/report")

    assert result["result_count"] == 1
    assert "测试页面" in result["summary"]
    assert "第一段。" in result["summary"]