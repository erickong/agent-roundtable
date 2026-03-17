"""Skills available to expert agents during roundtable meetings."""

from skills.web_search import SKILL_PROMPT, execute as web_search, is_available as search_available

__all__ = ["SKILL_PROMPT", "web_search", "search_available"]
