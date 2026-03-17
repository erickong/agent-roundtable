import pytest

from agents import (
    DEFAULT_AGENT_FETCH_CHARS,
    DEFAULT_AGENT_SEARCH_RESULTS,
    DEFAULT_AGENT_WEB_RESULTS,
    ExpertAgent,
    MAX_AGENT_SEARCHES,
)
from config import LLMProviderConfig


def _provider() -> LLMProviderConfig:
    return LLMProviderConfig(
        name="test",
        base_url="",
        api_key="test-key",
        model="test-model",
    )


@pytest.mark.anyio
async def test_expert_can_search_up_to_five_times_with_default_20_results():
    expert = ExpertAgent("专家A", "研究型", _provider())

    responses = [
        '{"search_query":"A股","core_position":"p1","key_points":["k1"],"main_risks":["r1"],"initial_suggestion":"s1"}',
        '{"search_query":"港股","core_position":"p2","key_points":["k2"],"main_risks":["r2"],"initial_suggestion":"s2"}',
        '{"search_query":{"query":"","category":"china_finance"},"core_position":"p3","key_points":["k3"],"main_risks":["r3"],"initial_suggestion":"s3"}',
        '{"search_query":"芯片","core_position":"p4","key_points":["k4"],"main_risks":["r4"],"initial_suggestion":"s4"}',
        '{"search_query":"关税","core_position":"p5","key_points":["k5"],"main_risks":["r5"],"initial_suggestion":"s5"}',
        '{"core_position":"final","key_points":["kf"],"main_risks":["rf"],"initial_suggestion":"sf"}',
    ]
    search_calls: list[dict] = []

    async def fake_call_llm(prompt: str) -> str:
        return responses.pop(0)

    async def fake_search(**kwargs):
        search_calls.append(kwargs)
        return {
            "summary": f"summary for {kwargs.get('query', '') or kwargs.get('category', 'ALL')}",
            "result_count": kwargs["max_results"],
        }

    expert._call_llm = fake_call_llm  # type: ignore[method-assign]
    expert.search_fn = fake_search

    result = await expert.speak_round1(topic="测试议题", opening="开场")

    assert len(search_calls) == MAX_AGENT_SEARCHES
    assert all(call["max_results"] == DEFAULT_AGENT_SEARCH_RESULTS for call in search_calls)
    assert result.content["core_position"] == "final"
    assert result.search_info is not None
    assert len(result.search_info) == MAX_AGENT_SEARCHES
    assert result.search_info[2]["query"] == "RECENT:china_finance"


@pytest.mark.anyio
async def test_expert_can_use_web_search_and_fetch_backends():
    expert = ExpertAgent("专家B", "基本面", _provider())

    responses = [
        '{"search_query":{"backend":"web","query":"鹏鼎控股 世运电路 东方财富","include_domains":["eastmoney.com"]},"core_position":"p1","key_points":["k1"],"main_risks":["r1"],"initial_suggestion":"s1"}',
        '{"search_query":{"backend":"fetch","url":"https://example.com/report"},"core_position":"p2","key_points":["k2"],"main_risks":["r2"],"initial_suggestion":"s2"}',
        '{"core_position":"final","key_points":["kf"],"main_risks":["rf"],"initial_suggestion":"sf"}',
    ]
    search_calls: list[dict] = []

    async def fake_call_llm(prompt: str) -> str:
        return responses.pop(0)

    async def fake_search(**kwargs):
        search_calls.append(kwargs)
        return {
            "summary": f"summary for {kwargs.get('backend')}:{kwargs.get('query') or kwargs.get('url')}",
            "result_count": kwargs.get("max_results", 1),
        }

    expert._call_llm = fake_call_llm  # type: ignore[method-assign]
    expert.search_fn = fake_search

    result = await expert.speak_round1(topic="测试议题", opening="开场")

    assert len(search_calls) == 2
    assert search_calls[0]["backend"] == "web"
    assert search_calls[0]["max_results"] == DEFAULT_AGENT_WEB_RESULTS
    assert search_calls[1]["backend"] == "fetch"
    assert search_calls[1]["max_chars"] == DEFAULT_AGENT_FETCH_CHARS
    assert result.search_info is not None
    assert result.search_info[0]["query"] == "WEB:鹏鼎控股 世运电路 东方财富"
    assert result.search_info[1]["query"] == "FETCH:https://example.com/report"