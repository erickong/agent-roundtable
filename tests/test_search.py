import pytest

import search


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

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

    async def get(self, url, params=None):
        return self.get_response(url, params)

    async def post(self, url, json=None):
        return self.post_response(url, json)


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
async def test_tavily_search_falls_back_to_recent_when_local_search_has_no_results(monkeypatch):
    monkeypatch.setattr(search, "get_search_api_url", lambda: "http://192.168.3.89:4001/v1/search")

    def build_client(*args, **kwargs):
        return FakeAsyncClient(
            get_response=lambda url, params: FakeResponse(
                {
                    "total": 1,
                    "results": [
                        {
                            "title": "China finance fallback",
                            "link": "https://example.com/fallback",
                            "summary": "Fallback story",
                            "published": "2026-03-17T00:00:00Z",
                            "source": "Test Source",
                            "category": "china_finance",
                        }
                    ],
                }
            ),
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

    assert result["result_count"] == 1
    assert "关键词未命中" in result["summary"]
    assert "China finance fallback" in result["summary"]