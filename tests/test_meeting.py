from agents import ExpertAgent
from config import LLMProviderConfig
from meeting import _build_expert_specs, _retitle_experts_from_opening


def _provider() -> LLMProviderConfig:
    return LLMProviderConfig(name="test", base_url="", api_key="dummy", model="dummy")


def test_retitle_experts_from_opening_updates_display_titles():
    experts = [
        ExpertAgent("创新专家", "创新型（偏提出新想法）", _provider()),
        ExpertAgent("审慎专家", "审慎型（偏发现漏洞）", _provider()),
    ]

    opening_text = """
【创新专家=>增长策略专家】
【审慎专家=>风险控制专家】
"""

    mapping = _retitle_experts_from_opening(experts, opening_text)

    assert mapping == {"创新专家": "增长策略专家", "审慎专家": "风险控制专家"}
    assert experts[0].name == "增长策略专家"
    assert experts[1].name == "风险控制专家"
    assert experts[0].base_name == "创新专家"
    assert "增长策略专家" in experts[0].system_prompt


def test_build_expert_specs_uses_base_roles_and_styles():
    experts = [
        ExpertAgent("创新专家", "创新型（偏提出新想法）", _provider()),
        ExpertAgent("工程专家", "工程型（偏落地实现）", _provider()),
    ]

    specs = _build_expert_specs(experts)

    assert "创新专家（创新型（偏提出新想法））" in specs
    assert "工程专家（工程型（偏落地实现））" in specs