"""Bilingual (Chinese / English) support utilities.

The system detects the language of the user's topic and adapts all
prompts, event messages, and agent labels accordingly.
"""


def is_chinese(text: str) -> bool:
    """Return True if *text* contains any CJK Unified Ideograph character."""
    return any("\u4e00" <= c <= "\u9fff" for c in text)


# ---------------------------------------------------------------------------
# Instruction appended to every LLM prompt so the model follows topic language
# ---------------------------------------------------------------------------
LANG_FOLLOW_INSTRUCTION = (
    "\n\nIMPORTANT language rule: "
    "Detect the language of the user's topic. "
    "If the topic is in English, you MUST respond entirely in English. "
    "If the topic is in Chinese, respond in Chinese. "
    "Always match the user's language throughout your entire response."
)


# ---------------------------------------------------------------------------
# Default expert configs – selected at runtime based on topic language
# ---------------------------------------------------------------------------
DEFAULT_EXPERTS_ZH = [
    {"name": "创新专家", "role_label": "创新型（偏提出新想法）"},
    {"name": "审慎专家", "role_label": "审慎型（偏发现漏洞）"},
    {"name": "工程专家", "role_label": "工程型（偏落地实现）"},
    {"name": "领域专家", "role_label": "专业型（偏领域知识）"},
]

DEFAULT_EXPERTS_EN = [
    {"name": "Innovation Expert", "role_label": "Innovative (generates new ideas)"},
    {"name": "Critical Expert", "role_label": "Critical (finds flaws and risks)"},
    {"name": "Engineering Expert", "role_label": "Practical (focuses on implementation)"},
    {"name": "Domain Expert", "role_label": "Specialist (deep domain knowledge)"},
]


def get_default_experts(topic: str) -> list[dict]:
    """Return the default expert list matching the topic language."""
    return DEFAULT_EXPERTS_ZH if is_chinese(topic) else DEFAULT_EXPERTS_EN


# ---------------------------------------------------------------------------
# Simple bilingual string helper
# ---------------------------------------------------------------------------
def t(topic: str, zh: str, en: str) -> str:
    """Pick *zh* or *en* based on whether *topic* looks Chinese."""
    return zh if is_chinese(topic) else en
