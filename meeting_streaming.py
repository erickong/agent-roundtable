"""Streaming orchestrator — sends events via a callback as the meeting progresses."""

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine, List

from models import MeetingInput, AgentMessage, RoundSummary, FinalReport
from agents import ModeratorAgent, ExpertAgent
from meeting import (
    _format_messages,
    _extract_attacks_on_agent,
    _build_compressed_summary,
    _build_expert_specs,
    _retitle_experts_from_opening,
)

logger = logging.getLogger(__name__)

# Type alias for the event callback
EventCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class StreamingMeeting:
    """Runs a roundtable meeting and streams events to a callback."""

    def __init__(
        self,
        moderator: ModeratorAgent,
        experts: List[ExpertAgent],
        max_rounds: int = 4,
        on_event: EventCallback | None = None,
    ):
        self.moderator = moderator
        self.experts = experts
        self.max_rounds = max_rounds
        self.messages: List[AgentMessage] = []
        self.summaries: List[RoundSummary] = []
        self._on_event = on_event
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    async def _emit(self, event: dict):
        if self._on_event:
            await self._on_event(event)

    def _check_cancel(self):
        if self._cancelled:
            raise asyncio.CancelledError("Meeting cancelled by user")

    async def run(self, meeting_input: MeetingInput) -> FinalReport | None:
        topic = meeting_input.topic

        await self._emit({
            "type": "system",
            "content": "会议开始，正在准备...",
        })

        # === Round 0: Opening ===
        self._check_cancel()
        await self._emit({"type": "round_start", "round": 0, "title": "Moderator 开场"})
        await self._emit({"type": "agent_start", "agent": "Moderator", "role": "仲裁者", "round": 0})

        expert_specs = _build_expert_specs(self.experts)
        opening_text = await self.moderator.opening(
            topic=topic,
            expert_specs=expert_specs,
            goal=meeting_input.goal,
            constraints=meeting_input.constraints,
            background=meeting_input.background,
        )
        self.opening_text = opening_text

        await self._emit({
            "type": "agent_message",
            "agent": "Moderator",
            "role": "仲裁者",
            "round": 0,
            "content": opening_text,
        })

        # Try to extract topic-specific expert titles from the opening and retitle agents
        _retitle_experts_from_opening(self.experts, opening_text)

        # === Round 1 ===
        self._check_cancel()
        await self._emit({"type": "round_start", "round": 1, "title": "第一轮：独立初始观点"})

        round1_messages = await self._run_expert_round(
            round_num=1,
            coro_fn=lambda expert: expert.speak_round1(topic=topic, opening=opening_text),
        )
        self.messages.extend(round1_messages)

        self._check_cancel()
        r1_formatted = _format_messages(round1_messages)
        summary1 = await self._run_moderator_summary(1, topic, r1_formatted)
        self.summaries.append(summary1)

        # === Round 2 ===
        self._check_cancel()
        await self._emit({"type": "round_start", "round": 2, "title": "第二轮：新观点 + 攻击弱点 + 保留亮点"})

        round2_messages = await self._run_expert_round(
            round_num=2,
            coro_fn=lambda expert: expert.speak_round2(
                topic=topic,
                round1_summary=r1_formatted,
                moderator_summary=summary1.raw_text,
            ),
        )
        self.messages.extend(round2_messages)

        self._check_cancel()
        r2_formatted = _format_messages(round2_messages)
        summary2 = await self._run_moderator_summary(2, topic, r2_formatted, summary1.raw_text)
        self.summaries.append(summary2)

        # === Round 3 ===
        self._check_cancel()
        await self._emit({"type": "round_start", "round": 3, "title": "第三轮：辩护 + 修正 + 收敛"})

        round3_messages = await self._run_expert_round(
            round_num=3,
            coro_fn=lambda expert: expert.speak_round3(
                topic=topic,
                round2_summary=r2_formatted,
                moderator_summary=summary2.raw_text,
                attacks_on_me=_extract_attacks_on_agent(round2_messages, expert.name),
            ),
        )
        self.messages.extend(round3_messages)

        self._check_cancel()
        r3_formatted = _format_messages(round3_messages)
        summary3 = await self._run_moderator_summary(3, topic, r3_formatted, summary2.raw_text)
        self.summaries.append(summary3)

        # === Round 4 (optional) ===
        if summary3.should_continue and self.max_rounds >= 4:
            self._check_cancel()
            await self._emit({"type": "round_start", "round": 4, "title": "第四轮：关键未解问题补充"})

            focused_issues = summary3.next_step
            compressed = _build_compressed_summary(self.summaries)

            round4_messages = await self._run_expert_round(
                round_num=4,
                coro_fn=lambda expert: expert.speak_round4(
                    topic=topic,
                    focused_issues=focused_issues,
                    compressed_summary=compressed,
                ),
            )
            self.messages.extend(round4_messages)

            self._check_cancel()
            r4_formatted = _format_messages(round4_messages)
            summary4 = await self._run_moderator_summary(4, topic, r4_formatted, summary3.raw_text)
            self.summaries.append(summary4)
        else:
            await self._emit({
                "type": "system",
                "content": "Moderator 判定讨论已充分收敛，跳过第四轮。",
            })

        # === Final Report ===
        self._check_cancel()
        await self._emit({"type": "round_start", "round": "final", "title": "最终报告"})
        await self._emit({"type": "agent_start", "agent": "Moderator", "role": "仲裁者", "round": "final"})

        all_discussion = self._build_full_discussion(opening_text)
        final_report = await self.moderator.finalize(topic=topic, all_discussion=all_discussion)

        await self._emit({
            "type": "final_report",
            "agent": "Moderator",
            "role": "仲裁者",
            "round": "final",
            "content": self._format_final_report(final_report),
            "data": {
                "problem_definition": final_report.problem_definition,
                "main_consensus": final_report.main_consensus,
                "main_disagreements": final_report.main_disagreements,
                "recommended_solution": final_report.recommended_solution,
                "why_this_solution": final_report.why_this_solution,
                "preserved_minority_opinions": final_report.preserved_minority_opinions,
                "agent_contributions": final_report.agent_contributions,
                "final_scores": [
                    {"agent_name": s.agent_name, "contribution_score": s.contribution_score, "summary": s.summary}
                    for s in final_report.final_scores
                ],
            },
        })

        await self._emit({"type": "meeting_end", "content": "会议结束。"})
        return final_report

    async def _run_expert_round(self, round_num: int, coro_fn) -> List[AgentMessage]:
        """Run all experts in parallel, emitting events for each."""
        for expert in self.experts:
            await self._emit({
                "type": "agent_start",
                "agent": expert.name,
                "round": round_num,
            })

        tasks = [coro_fn(expert) for expert in self.experts]
        results: list[AgentMessage | BaseException] = await asyncio.gather(*tasks, return_exceptions=True)

        messages = []
        for expert, result in zip(self.experts, results):
            if isinstance(result, BaseException):
                await self._emit({
                    "type": "agent_error",
                    "agent": expert.name,
                    "round": round_num,
                    "content": f"发言失败: {result}",
                })
                continue
            messages.append(result)
            # Format content nicely
            if result.content:
                content_str = json.dumps(result.content, ensure_ascii=False, indent=2)
            else:
                content_str = result.raw_text
            await self._emit({
                "type": "agent_message",
                "agent": expert.name,
                "round": round_num,
                "content": content_str,
            })
        return messages

    async def _run_moderator_summary(
        self, round_index: int, topic: str, round_messages: str, previous_context: str = ""
    ) -> RoundSummary:
        await self._emit({"type": "agent_start", "agent": "Moderator", "role": "仲裁者", "round": round_index})

        summary = await self.moderator.summarize_and_score(
            round_index=round_index,
            topic=topic,
            round_messages=round_messages,
            previous_context=previous_context,
        )

        # Build readable summary text
        parts = [f"## 第{round_index}轮总结\n"]
        if summary.new_valuable_ideas:
            parts.append("### 新增关键观点")
            for idea in summary.new_valuable_ideas:
                parts.append(f"- {idea}")
        if summary.strong_critiques:
            parts.append("\n### 有力攻击")
            for c in summary.strong_critiques:
                parts.append(f"- {c}")
        if summary.points_worth_preserving:
            parts.append("\n### 值得保留的观点")
            for p in summary.points_worth_preserving:
                parts.append(f"- {p}")
        if summary.scores:
            parts.append("\n### 评分")
            for s in summary.scores:
                parts.append(f"- **{s.agent_name}**: Novelty {s.novelty_score}/5 | Critique {s.critique_score}/5 — {s.comment}")
        parts.append(f"\n### 下一步\n{summary.next_step}")

        await self._emit({
            "type": "moderator_summary",
            "agent": "Moderator",
            "role": "仲裁者",
            "round": round_index,
            "content": "\n".join(parts),
        })
        return summary

    def _build_full_discussion(self, opening_text: str) -> str:
        parts = [f"## Moderator 开场\n{opening_text}"]
        for summary in self.summaries:
            round_idx = summary.round_index
            round_msgs = [m for m in self.messages if m.round_index == round_idx]
            parts.append(f"\n## 第{round_idx}轮发言")
            parts.append(_format_messages(round_msgs))
            parts.append(f"\n## 第{round_idx}轮 Moderator 总结")
            parts.append(summary.raw_text)
        return "\n\n".join(parts)

    @staticmethod
    def _format_final_report(report: FinalReport) -> str:
        lines = ["# 圆桌会议最终报告\n"]
        lines.append(f"## 1. 问题定义\n{report.problem_definition}\n")
        lines.append("## 2. 主要共识")
        for c in report.main_consensus:
            lines.append(f"- {c}")
        lines.append("\n## 3. 主要分歧")
        for d in report.main_disagreements:
            lines.append(f"- {d}")
        lines.append(f"\n## 4. 推荐方案\n{report.recommended_solution}\n")
        lines.append(f"## 5. 推荐理由\n{report.why_this_solution}\n")
        lines.append("## 6. 保留意见")
        for o in report.preserved_minority_opinions:
            lines.append(f"- {o}")
        lines.append("\n## 7. 专家贡献")
        for agent, contrib in report.agent_contributions.items():
            lines.append(f"- **{agent}**: {contrib}")
        lines.append("\n## 8. 最终评分")
        for s in report.final_scores:
            lines.append(f"- **{s.agent_name}**: {s.contribution_score}/10 — {s.summary}")
        return "\n".join(lines)
