"""Orchestrator for the Roundtable Meeting System V1."""

import asyncio
import json
import logging
import re
from typing import List

from models import MeetingInput, AgentMessage, RoundSummary, FinalReport
from agents import ModeratorAgent, ExpertAgent

logger = logging.getLogger(__name__)


def _format_messages(messages: List[AgentMessage]) -> str:
    """Format a list of AgentMessages into a readable summary string."""
    parts = []
    for msg in messages:
        content_str = json.dumps(msg.content, ensure_ascii=False, indent=2) if msg.content else msg.raw_text
        parts.append(f"【{msg.agent_name}】:\n{content_str}")
    return "\n\n".join(parts)


def _extract_attacks_on_agent(messages: List[AgentMessage], agent_name: str) -> str:
    """Extract all attacks directed at a specific agent from round 2 messages."""
    attacks = []
    for msg in messages:
        if not msg.content:
            continue
        for attack in msg.content.get("attacks", []):
            if isinstance(attack, dict) and attack.get("target_agent") == agent_name:
                attacks.append(
                    f"{msg.agent_name} 攻击了你的观点 \"{attack.get('target_point', '')}\"，"
                    f"理由：{attack.get('weakness', '')}"
                )
    return "\n".join(attacks) if attacks else "本轮没有人直接攻击你的观点。"


def _build_compressed_summary(summaries: List[RoundSummary]) -> str:
    """Build a compressed summary of all previous rounds for Round 4 context."""
    parts = []
    for s in summaries:
        ideas = "; ".join(s.new_valuable_ideas) if s.new_valuable_ideas else "无"
        critiques = "; ".join(s.strong_critiques) if s.strong_critiques else "无"
        preserved = "; ".join(s.points_worth_preserving) if s.points_worth_preserving else "无"
        parts.append(
            f"第{s.round_index}轮 —— "
            f"新观点: {ideas} | "
            f"关键攻击: {critiques} | "
            f"值得保留: {preserved}"
        )
    return "\n".join(parts)


def _build_expert_specs(experts: List[ExpertAgent]) -> str:
    """Build moderator-facing expert specs for opening role assignment."""
    return "\n".join(f"- {expert.opening_assignment_label}" for expert in experts)


def _clean_expert_title(raw_title: str) -> str:
    title = raw_title.strip().strip("】]").strip()
    title = title.strip("“”\"'[]【】")
    title = re.split(r"[，。；;]", title, maxsplit=1)[0].strip()
    return title


def _retitle_experts_from_opening(experts: List[ExpertAgent], opening_text: str) -> dict[str, str]:
    """Extract topic-specific expert titles from the moderator opening."""
    retitled: dict[str, str] = {}
    for expert in experts:
        source_label = expert.base_name
        patterns = [
            rf"[【\[]\s*{re.escape(source_label)}\s*(?:=>|->|→|:|：)\s*(?P<title>[^\]】\n]{{2,30}})\s*[】\]]",
            rf"{re.escape(source_label)}\s*(?:=>|->|→|:|：)\s*(?P<title>[^\n]{{2,30}})",
        ]
        new_title = ""
        for line in opening_text.splitlines():
            for pattern in patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if not match:
                    continue
                candidate = _clean_expert_title(match.group("title"))
                if candidate:
                    new_title = candidate
                    break
            if new_title:
                break
        if new_title and new_title != expert.name:
            old_name = expert.name
            expert.retitle(new_title)
            retitled[old_name] = new_title
    if retitled:
        logger.info("Expert title mapping: %s", retitled)
    return retitled


class RoundtableMeeting:
    """Orchestrates a multi-agent roundtable meeting."""

    def __init__(
        self,
        moderator: ModeratorAgent,
        experts: List[ExpertAgent],
        max_rounds: int = 4,
    ):
        self.moderator = moderator
        self.experts = experts
        self.max_rounds = max_rounds
        self.messages: List[AgentMessage] = []
        self.summaries: List[RoundSummary] = []

    def _round_messages(self, round_index: int) -> List[AgentMessage]:
        return [m for m in self.messages if m.round_index == round_index]

    async def run(self, meeting_input: MeetingInput) -> FinalReport:
        """Run the full roundtable meeting and return the final report."""
        topic = meeting_input.topic
        logger.info("=== Roundtable Meeting Start: %s ===", topic)

        # Round 0: Moderator opening
        expert_specs = _build_expert_specs(self.experts)
        opening_text = await self.moderator.opening(
            topic=topic,
            expert_specs=expert_specs,
            goal=meeting_input.goal,
            constraints=meeting_input.constraints,
            background=meeting_input.background,
        )
        logger.info("Moderator opening:\n%s", opening_text)
        _retitle_experts_from_opening(self.experts, opening_text)

        # Round 1: Independent initial opinions
        logger.info("=== Round 1: Independent Opinions ===")
        round1_tasks = [
            expert.speak_round1(topic=topic, opening=opening_text)
            for expert in self.experts
        ]
        round1_messages = await asyncio.gather(*round1_tasks)
        self.messages.extend(round1_messages)
        for msg in round1_messages:
            logger.info("[Round 1] %s: %s", msg.agent_name, msg.raw_text[:200])

        # Moderator summary + score for Round 1
        r1_formatted = _format_messages(round1_messages)
        summary1 = await self.moderator.summarize_and_score(
            round_index=1,
            topic=topic,
            round_messages=r1_formatted,
        )
        self.summaries.append(summary1)
        logger.info("Round 1 summary:\n%s", summary1.raw_text[:300])

        # Round 2: New points + attacks + preserve
        logger.info("=== Round 2: Critique & New Points ===")
        round2_tasks = [
            expert.speak_round2(
                topic=topic,
                round1_summary=r1_formatted,
                moderator_summary=summary1.raw_text,
            )
            for expert in self.experts
        ]
        round2_messages = await asyncio.gather(*round2_tasks)
        self.messages.extend(round2_messages)
        for msg in round2_messages:
            logger.info("[Round 2] %s: %s", msg.agent_name, msg.raw_text[:200])

        # Moderator summary + score for Round 2
        r2_formatted = _format_messages(round2_messages)
        summary2 = await self.moderator.summarize_and_score(
            round_index=2,
            topic=topic,
            round_messages=r2_formatted,
            previous_context=summary1.raw_text,
        )
        self.summaries.append(summary2)
        logger.info("Round 2 summary:\n%s", summary2.raw_text[:300])

        # Round 3: Defense + revision + convergence
        logger.info("=== Round 3: Defense & Convergence ===")
        round3_tasks = [
            expert.speak_round3(
                topic=topic,
                round2_summary=r2_formatted,
                moderator_summary=summary2.raw_text,
                attacks_on_me=_extract_attacks_on_agent(round2_messages, expert.name),
            )
            for expert in self.experts
        ]
        round3_messages = await asyncio.gather(*round3_tasks)
        self.messages.extend(round3_messages)
        for msg in round3_messages:
            logger.info("[Round 3] %s: %s", msg.agent_name, msg.raw_text[:200])

        # Moderator summary + score for Round 3
        r3_formatted = _format_messages(round3_messages)
        summary3 = await self.moderator.summarize_and_score(
            round_index=3,
            topic=topic,
            round_messages=r3_formatted,
            previous_context=summary2.raw_text,
        )
        self.summaries.append(summary3)
        logger.info("Round 3 summary:\n%s", summary3.raw_text[:300])

        # Round 4 (optional): only if moderator says to continue
        if summary3.should_continue and self.max_rounds >= 4:
            logger.info("=== Round 4: Focused Supplement ===")
            focused_issues = summary3.next_step
            compressed = _build_compressed_summary(self.summaries)

            round4_tasks = [
                expert.speak_round4(
                    topic=topic,
                    focused_issues=focused_issues,
                    compressed_summary=compressed,
                )
                for expert in self.experts
            ]
            round4_messages = await asyncio.gather(*round4_tasks)
            self.messages.extend(round4_messages)
            for msg in round4_messages:
                logger.info("[Round 4] %s: %s", msg.agent_name, msg.raw_text[:200])

            r4_formatted = _format_messages(round4_messages)
            summary4 = await self.moderator.summarize_and_score(
                round_index=4,
                topic=topic,
                round_messages=r4_formatted,
                previous_context=summary3.raw_text,
            )
            self.summaries.append(summary4)
        else:
            logger.info("Round 4 skipped — moderator decided discussion has converged.")

        # Final report
        logger.info("=== Generating Final Report ===")
        all_discussion = self._build_full_discussion(opening_text)
        final_report = await self.moderator.finalize(
            topic=topic, all_discussion=all_discussion
        )
        logger.info("Final report generated.")
        return final_report

    def _build_full_discussion(self, opening_text: str) -> str:
        """Build the full discussion text for the final report."""
        parts = [f"## Moderator 开场\n{opening_text}"]

        for summary in self.summaries:
            round_idx = summary.round_index
            round_msgs = self._round_messages(round_idx)
            parts.append(f"\n## 第{round_idx}轮发言")
            parts.append(_format_messages(round_msgs))
            parts.append(f"\n## 第{round_idx}轮 Moderator 总结")
            parts.append(summary.raw_text)

        return "\n\n".join(parts)
