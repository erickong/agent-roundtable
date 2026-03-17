"""Entry point for the Roundtable Meeting System V1."""

import asyncio
import logging
import sys

from config import load_config
from models import MeetingInput
from agents import ModeratorAgent, ExpertAgent
from meeting import RoundtableMeeting
from i18n import t, get_default_experts, LANG_FOLLOW_INSTRUCTION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Default expert configurations — selected at runtime based on topic language
DEFAULT_EXPERTS = get_default_experts  # function; called with topic


def _render_final_report_markdown(report) -> str:
    """Render FinalReport as a readable markdown string."""
    lines = ["# Final Roundtable Report", ""]
    lines.append("## 1. Problem Definition")
    lines.append(report.problem_definition)
    lines.append("")
    lines.append("## 2. Main Consensus")
    for item in report.main_consensus:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 3. Main Disagreements")
    for item in report.main_disagreements:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 4. Recommended Solution")
    lines.append(report.recommended_solution)
    lines.append("")
    lines.append("## 5. Why This Solution")
    lines.append(report.why_this_solution)
    lines.append("")
    lines.append("## 6. Preserved Minority Opinions")
    for item in report.preserved_minority_opinions:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 7. Agent Contributions")
    for agent, contribution in report.agent_contributions.items():
        lines.append(f"- **{agent}**: {contribution}")
    lines.append("")
    lines.append("## 8. Final Scores")
    for score in report.final_scores:
        lines.append(f"- **{score.agent_name}**: Contribution {score.contribution_score}/10 — {score.summary}")
    lines.append("")
    return "\n".join(lines)


async def _research_phase_cli(topic: str, background: str | None, env_path: str | None = None) -> str:
    """Run the research phase via CLI, similar to web_server._research_phase."""
    from openai import AsyncOpenAI
    from search import tavily_search, get_tavily_api_key

    if not get_tavily_api_key():
        logger.warning("Tavily API key not set — skipping web search")
        return ""

    config = load_config(env_path)
    client = AsyncOpenAI(
        api_key=config.moderator_llm.api_key,
        base_url=config.moderator_llm.base_url or None,
    )
    model = config.moderator_llm.model

    max_searches = 5
    collected_info: list[str] = []
    searched_queries: list[str] = []

    print("🔍 Moderator is researching background information...")

    for i in range(max_searches):
        _t = lambda zh, en: t(topic, zh, en)
        prompt = (
            f"{_t('你是圆桌会议的仲裁者。你需要为即将讨论的议题搜索背景信息。', 'You are the moderator of a roundtable meeting. You need to search for background information on the upcoming topic.')}\n\n"
            f"{_t('议题', 'Topic')}：{topic}\n"
        )
        if background:
            prompt += f"{_t('用户提供的背景', 'User-provided background')}：{background}\n"
        if collected_info:
            prompt += f"\n{_t('已搜集到的信息', 'Collected information so far')}：\n{''.join(collected_info[-3:])}\n"
        if searched_queries:
            prompt += f"\n{_t('已搜索过的关键词', 'Previously searched keywords')}：{', '.join(searched_queries)}\n"
        prompt += (
            f"\n{_t(f'这是第 {i + 1}/{max_searches} 次搜索机会。', f'This is search opportunity {i + 1}/{max_searches}.')}"
            f"{_t('请判断是否还需要搜索更多信息。', 'Please decide whether more searching is needed.')}\n"
            f"{_t('如果信息已经足够充分，回复：DONE', 'If information is sufficient, reply: DONE')}\n"
            f"{_t('如果还需搜索，回复一个简短的搜索关键词（不要回复其他内容，只回复关键词）。', 'If more search is needed, reply with a short search keyword only (no other text).')}"
        )

        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": t(topic, "你是圆桌会议的研究助手。根据议题判断是否需要搜索，并生成精准的搜索关键词。", "You are a roundtable meeting research assistant. Decide whether to search and generate precise search keywords based on the topic.")},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            query = (resp.choices[0].message.content or "").strip()
        except Exception as e:
            logger.warning("Research LLM call failed: %s", e)
            break

        if not query or query.upper() == "DONE":
            print("🔍 Moderator determined information is sufficient.")
            break

        searched_queries.append(query)
        print(f"🔍 Search ({i + 1}/{max_searches}): {query}")

        try:
            result = await tavily_search(query=query, max_results=5, topic="news")
            collected_info.append(result["summary"])
            print(f"  → Found {result['result_count']} results")
        except Exception as e:
            logger.warning("Search failed for query '%s': %s", query, e)

    if collected_info:
        print(f"🔍 Research complete ({len(searched_queries)} searches). Starting meeting...\n")
    return "\n\n".join(collected_info)


async def run_meeting(
    topic: str,
    goal: str | None = None,
    constraints: list[str] | None = None,
    background: str | None = None,
    expert_configs: list[dict] | None = None,
    max_rounds: int = 4,
    env_path: str | None = None,
    search_enabled: bool = False,
) -> str:
    """Run a roundtable meeting and return the final report as markdown.

    Args:
        topic: The discussion topic.
        goal: Optional goal for the meeting.
        constraints: Optional list of constraints.
        background: Optional background information.
        expert_configs: List of dicts with 'name' and 'role_label' keys.
        max_rounds: Maximum number of rounds (default 4).
        env_path: Path to .env file (defaults to .env in cwd).
        search_enabled: Whether to run web search before meeting.

    Returns:
        The final report as a markdown string.
    """
    config = load_config(env_path)

    # Research phase if search is enabled
    search_background = ""
    if search_enabled:
        search_background = await _research_phase_cli(topic, background, env_path)

    combined_background = ""
    if background:
        combined_background = background
    if search_background:
        combined_background = (combined_background + "\n\n" if combined_background else "") + search_background

    meeting_input = MeetingInput(
        topic=topic,
        goal=goal,
        constraints=constraints or [],
        background=combined_background or None,
    )

    # Moderator uses its own dedicated LLM
    moderator = ModeratorAgent(provider=config.moderator_llm)

    # Each expert gets a provider selected by weight
    configs = expert_configs or DEFAULT_EXPERTS(topic)
    experts = [
        ExpertAgent(
            name=cfg["name"],
            role_label=cfg["role_label"],
            provider=config.select_expert_provider(),
        )
        for cfg in configs
    ]

    for expert in experts:
        logger.info(
            "Expert [%s] using provider: %s (%s)",
            expert.name, expert.provider.name, expert.provider.model,
        )

    meeting = RoundtableMeeting(
        moderator=moderator,
        experts=experts,
        max_rounds=max_rounds,
    )

    final_report = await meeting.run(meeting_input)

    # Output as markdown
    if final_report.raw_markdown and not final_report.problem_definition:
        return final_report.raw_markdown

    return _render_final_report_markdown(final_report)


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        print("Usage: python main.py <topic> [options]")
        print()
        print("Options:")
        print("  --goal <goal>         Meeting goal")
        print("  --background <text>   Background information")
        print("  --env <path>          Path to .env file")
        print("  --constraint <text>   Add constraint (repeatable)")
        print("  --search              Enable web search before meeting")
        print("  --no-chat             Skip post-meeting interactive chat")
        print()
        print("Examples:")
        print('  python main.py "How to design a multi-agent stock research system?"')
        print('  python main.py "Topic" --goal "Find 3 solutions" --search')
        print('  python main.py "Topic" --env /path/to/.env --no-chat')
        sys.exit(1)

    topic = sys.argv[1]
    goal = None
    background = None
    env_path = None
    constraints = []
    search_enabled = False
    interactive_chat = True

    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--goal" and i + 1 < len(sys.argv):
            goal = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--background" and i + 1 < len(sys.argv):
            background = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--env" and i + 1 < len(sys.argv):
            env_path = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--constraint" and i + 1 < len(sys.argv):
            constraints.append(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--search":
            search_enabled = True
            i += 1
        elif sys.argv[i] == "--no-chat":
            interactive_chat = False
            i += 1
        else:
            print(f"Unknown argument: {sys.argv[i]}")
            sys.exit(1)

    # Startup validation
    from search import get_tavily_api_key
    try:
        config = load_config(env_path)
        logger.info("✓ Moderator LLM: %s (%s)", config.moderator_llm.name, config.moderator_llm.model)
        logger.info("✓ Expert providers: %d configured", len(config.expert_providers))
        if get_tavily_api_key():
            logger.info("✓ Tavily API key configured")
        elif search_enabled:
            logger.warning("✗ Tavily API key not set — --search will be skipped")
            search_enabled = False
    except (ValueError, FileNotFoundError) as e:
        logger.error("Configuration error: %s", e)
        logger.error("First run: copy .env.example to .env and fill in the required keys.")
        sys.exit(1)

    result = asyncio.run(
        run_meeting(
            topic=topic,
            goal=goal,
            constraints=constraints,
            background=background,
            env_path=env_path,
            search_enabled=search_enabled,
        )
    )

    print(result)

    # Save to file
    output_path = "meeting_report.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)
    logger.info("Report saved to %s", output_path)

    # Post-meeting interactive chat
    if interactive_chat:
        _interactive_chat(result, env_path)


def _interactive_chat(meeting_report: str, env_path: str | None = None):
    """Interactive post-meeting chat with the moderator."""
    from openai import OpenAI
    import httpx

    print("\n" + "=" * 60)
    print("Meeting complete. You can now chat with the moderator.")
    print("Type 'quit' or 'exit' to end. Press Ctrl+C to abort.")
    print("=" * 60 + "\n")

    config = load_config(env_path)
    kwargs: dict = {"api_key": config.moderator_llm.api_key}
    if config.moderator_llm.base_url:
        kwargs["base_url"] = config.moderator_llm.base_url
    if config.moderator_llm.timeout:
        kwargs["timeout"] = httpx.Timeout(config.moderator_llm.timeout, connect=30.0)
    client = OpenAI(**kwargs)

    system_prompt = (
        t(meeting_report,
          "你是刚刚结束的圆桌会议的仲裁者。以下是完整的会议报告。\n"
          "用户现在对会议内容有后续问题，请基于会议报告回答。\n"
          "保持中立、准确、简洁。如果用户问到会议中没有讨论的内容，请如实说明。\n\n",
          "You are the moderator of a roundtable meeting that just ended. Below is the full meeting report.\n"
          "The user has follow-up questions about the meeting. Answer based on the report.\n"
          "Stay neutral, accurate, and concise. If the user asks about something not discussed, say so.\n\n"
        )
        + f"=== Meeting Report ===\n{meeting_report}\n=== End of Report ==="
        + LANG_FOLLOW_INSTRUCTION
    )
    chat_history = [{"role": "system", "content": system_prompt}]

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit"):
            print("Goodbye!")
            break

        chat_history.append({"role": "user", "content": user_input})

        try:
            resp = client.chat.completions.create(
                model=config.moderator_llm.model,
                messages=chat_history,
                temperature=0.5,
            )
            reply = resp.choices[0].message.content or ""
            chat_history.append({"role": "assistant", "content": reply})
            print(f"\nModerator: {reply}\n")
        except Exception as e:
            print(f"\nError: {e}\n")
            chat_history.pop()  # Remove the failed user message


if __name__ == "__main__":
    main()
