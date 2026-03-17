"""Agent interfaces for the Roundtable Meeting System V1."""

import json
import logging
from typing import Any, Callable, Coroutine, Dict, Optional

import httpx
from openai import AsyncOpenAI

from config import LLMProviderConfig
from models import AgentMessage, RoundScore, RoundSummary, FinalReport, FinalScore
from parser import parse_json_response, safe_parse_agent_output
from i18n import LANG_FOLLOW_INSTRUCTION
from skills.web_search import (
    SEARCH_DUPLICATE_PROMPT,
    SEARCH_ERROR_PROMPT,
    SEARCH_FINAL_ONLY_PROMPT,
    SEARCH_KEYWORD_FIRST_PROMPT,
    SEARCH_REFINE_PROMPT,
    SKILL_PROMPT as SEARCH_SKILL_PROMPT,
)
from prompts import (
    MODERATOR_OPENING_PROMPT,
    MODERATOR_SUMMARY_PROMPT,
    MODERATOR_FINAL_PROMPT,
    EXPERT_ROUND1_PROMPT,
    EXPERT_ROUND2_PROMPT,
    EXPERT_ROUND3_PROMPT,
    EXPERT_ROUND4_PROMPT,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 1
MAX_AGENT_SEARCHES = 5
DEFAULT_AGENT_SEARCH_RESULTS = 20


def _build_search_request(search_query: Any) -> tuple[dict[str, Any], str, str]:
    if isinstance(search_query, dict):
        params = dict(search_query)
    else:
        params = {"query": str(search_query)}

    query_value = str(params.get("query", "") or "")
    if query_value.upper() == "ALL":
        params["query"] = ""
    if not params.get("max_results"):
        params["max_results"] = DEFAULT_AGENT_SEARCH_RESULTS

    display = query_value or "ALL"
    if not params.get("query") and params.get("category"):
        display = f"RECENT:{params['category']}"
    if params.get("source"):
        display = f"{display} @ {params['source']}"

    request_key = json.dumps(
        {
            "query": params.get("query", ""),
            "category": params.get("category"),
            "source": params.get("source"),
            "days": params.get("days"),
            "max_results": params.get("max_results"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return params, display, request_key


def create_client(provider: LLMProviderConfig) -> AsyncOpenAI:
    """Create an AsyncOpenAI client from a provider config."""
    kwargs: dict = {"api_key": provider.api_key}
    if provider.base_url:
        kwargs["base_url"] = provider.base_url
    if provider.timeout:
        kwargs["timeout"] = httpx.Timeout(provider.timeout, connect=30.0)
    return AsyncOpenAI(**kwargs)


class BaseMeetingAgent:
    """Base class for all meeting agents."""

    def __init__(
        self,
        name: str,
        role_label: str,
        system_prompt: str,
        provider: LLMProviderConfig,
    ):
        self.name = name
        self.role_label = role_label
        self.system_prompt = system_prompt
        self.provider = provider
        self.client = create_client(provider)
        self.model = provider.model

    async def _call_llm(self, user_prompt: str) -> str:
        """Call the LLM and return the raw text response."""
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.7,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                logger.warning(
                    "[%s] LLM call attempt %d failed (%s/%s): %s",
                    self.name, attempt + 1, self.provider.name, self.model, e,
                )
                if attempt == MAX_RETRIES:
                    raise
        return ""

    async def speak(
        self, round_index: int, task_prompt: str, context: dict
    ) -> AgentMessage:
        """Generate a speech for the given round."""
        raw_text = await self._call_llm(task_prompt)
        content = safe_parse_agent_output(raw_text, list(context.get("expected_keys", [])))
        return AgentMessage(
            round_index=round_index,
            agent_name=self.name,
            content=content,
            raw_text=raw_text,
        )


class ExpertAgent(BaseMeetingAgent):
    """An expert agent participating in the roundtable."""

    # Async search callback: async (query: str) -> dict with 'summary', 'result_count'
    search_fn: Optional[Callable[..., Coroutine]] = None

    def __init__(
        self,
        name: str,
        role_label: str,
        provider: LLMProviderConfig,
    ):
        self.base_name = name
        self.style_label = role_label
        super().__init__(name, role_label, "", provider)
        self._refresh_system_prompt()

    def _refresh_system_prompt(self):
        self.system_prompt = (
            f"你是一位圆桌会议专家，当前在本议题中的对外头衔是 [{self.name}]，"
            f"你的基础思考风格是 [{self.style_label}]。"
            "请始终保持该思考风格参与讨论，提出建设性、有深度的观点，"
            "并在表述身份时以当前对外头衔为准。"
            + LANG_FOLLOW_INSTRUCTION
        )

    @property
    def _skill_prompt(self) -> str:
        """Return skill prompt text if search is available, empty string otherwise."""
        return SEARCH_SKILL_PROMPT if self.search_fn else ""

    async def _speak_with_search(
        self, prompt: str, expected_keys: list[str], round_index: int,
    ) -> AgentMessage:
        """Multi-step speak: LLM may search up to MAX_AGENT_SEARCHES times, then answer."""
        raw = await self._call_llm(prompt)
        content = safe_parse_agent_output(raw, expected_keys + ["search_query"])

        search_info: list[dict[str, Any]] = []
        search_history: list[str] = []
        seen_requests: set[str] = set()
        executed_searches = 0
        keyword_searches = 0
        decision_rounds = 0

        if self.search_fn:
            while executed_searches < MAX_AGENT_SEARCHES and decision_rounds < MAX_AGENT_SEARCHES * 3:
                decision_rounds += 1
                search_query = content.pop("search_query", None) if isinstance(content, dict) else None
                if not search_query:
                    break

                params, query_display, request_key = _build_search_request(search_query)
                remaining_searches = MAX_AGENT_SEARCHES - executed_searches - 1
                is_browse_request = not str(params.get("query", "") or "").strip()

                if is_browse_request and keyword_searches == 0:
                    search_history.append(
                        SEARCH_KEYWORD_FIRST_PROMPT.format(
                            remaining_searches=MAX_AGENT_SEARCHES - executed_searches,
                        )
                    )
                    raw = await self._call_llm(prompt + "".join(search_history))
                    content = safe_parse_agent_output(raw, expected_keys + ["search_query"])
                    continue

                if request_key in seen_requests:
                    logger.info("[%s] Round %d duplicate search skipped: '%s'", self.name, round_index, query_display)
                    search_history.append(
                        SEARCH_DUPLICATE_PROMPT.format(
                            query=query_display,
                            remaining_searches=remaining_searches,
                        )
                    )
                    if remaining_searches == 0:
                        search_history.append(SEARCH_FINAL_ONLY_PROMPT)
                        raw = await self._call_llm(prompt + "".join(search_history))
                        content = safe_parse_agent_output(raw, expected_keys)
                        break
                    raw = await self._call_llm(prompt + "".join(search_history))
                    content = safe_parse_agent_output(raw, expected_keys + ["search_query"])
                    continue

                seen_requests.add(request_key)
                executed_searches += 1
                if not is_browse_request:
                    keyword_searches += 1

                try:
                    search_result = await self.search_fn(**params)
                    search_info.append(
                        {
                            "query": query_display,
                            "result_count": search_result.get("result_count", 0),
                            "search_index": executed_searches,
                            "max_searches": MAX_AGENT_SEARCHES,
                        }
                    )
                    logger.info(
                        "[%s] Round %d search %d/%d: '%s' → %d results",
                        self.name,
                        round_index,
                        executed_searches,
                        MAX_AGENT_SEARCHES,
                        query_display,
                        search_info[-1]["result_count"],
                    )
                    search_history.append(
                        SEARCH_REFINE_PROMPT.format(
                            query=query_display,
                            summary=search_result.get("summary", ""),
                            search_index=executed_searches,
                            max_searches=MAX_AGENT_SEARCHES,
                            remaining_searches=remaining_searches,
                        )
                    )
                except Exception as e:
                    logger.warning("[%s] Search failed for '%s': %s", self.name, query_display, e)
                    search_history.append(
                        SEARCH_ERROR_PROMPT.format(
                            query=query_display,
                            error=e,
                            remaining_searches=remaining_searches,
                        )
                    )

                if remaining_searches == 0:
                    search_history.append(SEARCH_FINAL_ONLY_PROMPT)
                    raw = await self._call_llm(prompt + "".join(search_history))
                    content = safe_parse_agent_output(raw, expected_keys)
                    break

                raw = await self._call_llm(prompt + "".join(search_history))
                content = safe_parse_agent_output(raw, expected_keys + ["search_query"])

        if isinstance(content, dict):
            content.pop("search_query", None)

        return AgentMessage(
            round_index=round_index,
            agent_name=self.name,
            content=content,
            raw_text=raw,
            search_info=search_info or None,
        )

    def retitle(self, title: str):
        title = title.strip()
        if not title or title == self.name:
            return
        self.name = title
        self._refresh_system_prompt()

    @property
    def opening_assignment_label(self) -> str:
        return f"{self.base_name}（{self.style_label}）"

    async def speak_round1(self, topic: str, opening: str) -> AgentMessage:
        prompt = EXPERT_ROUND1_PROMPT.format(
            role_label=self.role_label, topic=topic, opening=opening,
            skill_prompt=self._skill_prompt,
        )
        return await self._speak_with_search(
            prompt, ["core_position", "key_points", "main_risks", "initial_suggestion"], 1,
        )

    async def speak_round2(
        self, topic: str, round1_summary: str, moderator_summary: str
    ) -> AgentMessage:
        prompt = EXPERT_ROUND2_PROMPT.format(
            role_label=self.role_label,
            topic=topic,
            round1_summary=round1_summary,
            moderator_summary=moderator_summary,
            skill_prompt=self._skill_prompt,
        )
        return await self._speak_with_search(
            prompt, ["new_points", "attacks", "preserved_points"], 2,
        )

    async def speak_round3(
        self,
        topic: str,
        round2_summary: str,
        moderator_summary: str,
        attacks_on_me: str,
    ) -> AgentMessage:
        prompt = EXPERT_ROUND3_PROMPT.format(
            role_label=self.role_label,
            topic=topic,
            round2_summary=round2_summary,
            moderator_summary=moderator_summary,
            agent_name=self.name,
            attacks_on_me=attacks_on_me,
            skill_prompt=self._skill_prompt,
        )
        return await self._speak_with_search(
            prompt,
            ["strongest_attack_on_me", "accepted_criticisms", "revisions",
             "final_position", "preferred_solution"],
            3,
        )

    async def speak_round4(
        self, topic: str, focused_issues: str, compressed_summary: str
    ) -> AgentMessage:
        prompt = EXPERT_ROUND4_PROMPT.format(
            role_label=self.role_label,
            topic=topic,
            focused_issues=focused_issues,
            compressed_summary=compressed_summary,
            skill_prompt=self._skill_prompt,
        )
        return await self._speak_with_search(
            prompt,
            ["focused_issue", "final_addition", "last_attack_or_defense", "closing_view"],
            4,
        )


class ModeratorAgent(BaseMeetingAgent):
    """The moderator/arbiter of the roundtable meeting."""

    def __init__(
        self,
        provider: LLMProviderConfig,
        name: str = "Moderator",
    ):
        system_prompt = (
            "你是圆桌会议的仲裁者和主持人。你的职责是引导话题、每轮做总结、"
            "为每个专家打分，并在最终输出推荐方案。保持中立，不深度参与具体业务分析。"
            + LANG_FOLLOW_INSTRUCTION
        )
        super().__init__(name, "仲裁者", system_prompt, provider)

    async def opening(self, topic: str, expert_specs: str, goal: Optional[str], constraints: list, background: Optional[str]) -> str:
        goal_section = f"目标：{goal}" if goal else ""
        constraints_section = (
            "限制条件：\n" + "\n".join(f"- {c}" for c in constraints)
            if constraints
            else ""
        )
        background_section = f"背景信息：{background}" if background else ""

        prompt = MODERATOR_OPENING_PROMPT.format(
            topic=topic,
            expert_specs=expert_specs,
            goal_section=goal_section,
            constraints_section=constraints_section,
            background_section=background_section,
        )
        return await self._call_llm(prompt)

    async def summarize_and_score(
        self,
        round_index: int,
        topic: str,
        round_messages: str,
        previous_context: str = "",
    ) -> RoundSummary:
        prompt = MODERATOR_SUMMARY_PROMPT.format(
            round_index=round_index,
            topic=topic,
            round_messages=round_messages,
            previous_context=(
                f"\n前面轮次的上下文：\n{previous_context}" if previous_context else ""
            ),
        )
        raw = await self._call_llm(prompt)
        parsed = parse_json_response(raw)

        if parsed is None:
            logger.warning("Failed to parse moderator summary, using raw text")
            return RoundSummary(
                round_index=round_index,
                new_valuable_ideas=[],
                strong_critiques=[],
                points_worth_preserving=[],
                scores=[],
                next_step="",
                should_continue=round_index < 3,
                raw_text=raw,
            )

        scores = []
        for s in parsed.get("scores", []):
            scores.append(
                RoundScore(
                    round_index=round_index,
                    agent_name=s.get("agent_name", ""),
                    novelty_score=int(s.get("novelty_score", 0)),
                    critique_score=int(s.get("critique_score", 0)),
                    comment=s.get("comment", ""),
                )
            )

        return RoundSummary(
            round_index=round_index,
            new_valuable_ideas=parsed.get("new_valuable_ideas", []),
            strong_critiques=parsed.get("strong_critiques", []),
            points_worth_preserving=parsed.get("points_worth_preserving", []),
            scores=scores,
            next_step=parsed.get("next_step", ""),
            should_continue=parsed.get("should_continue", round_index < 3),
            raw_text=raw,
        )

    async def finalize(self, topic: str, all_discussion: str) -> FinalReport:
        prompt = MODERATOR_FINAL_PROMPT.format(
            topic=topic, all_discussion=all_discussion
        )
        raw = await self._call_llm(prompt)
        parsed = parse_json_response(raw)

        if parsed is None:
            logger.warning("Failed to parse final report, using raw text")
            return FinalReport(
                problem_definition=topic,
                main_consensus=[],
                main_disagreements=[],
                recommended_solution="",
                why_this_solution="",
                preserved_minority_opinions=[],
                agent_contributions={},
                final_scores=[],
                raw_markdown=raw,
            )

        final_scores = []
        for s in parsed.get("final_scores", []):
            final_scores.append(
                FinalScore(
                    agent_name=s.get("agent_name", ""),
                    contribution_score=int(s.get("contribution_score", 0)),
                    summary=s.get("summary", ""),
                )
            )

        return FinalReport(
            problem_definition=parsed.get("problem_definition", ""),
            main_consensus=parsed.get("main_consensus", []),
            main_disagreements=parsed.get("main_disagreements", []),
            recommended_solution=parsed.get("recommended_solution", ""),
            why_this_solution=parsed.get("why_this_solution", ""),
            preserved_minority_opinions=parsed.get("preserved_minority_opinions", []),
            agent_contributions=parsed.get("agent_contributions", {}),
            final_scores=final_scores,
            raw_markdown=raw,
        )
