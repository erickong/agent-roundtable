"""Entry point for the Roundtable Meeting System V1."""

import asyncio
import logging
import sys

from config import load_config
from models import MeetingInput
from agents import ModeratorAgent, ExpertAgent
from meeting import RoundtableMeeting
from i18n import t, get_default_experts, LANG_FOLLOW_INSTRUCTION
from skills.web_search import execute as skill_web_search, is_available as skill_search_available

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
    from search import (
        fetch_webpage_content,
        get_search_api_url,
        get_web_search_api_url,
        parse_research_command,
        tavily_search,
        web_search,
    )

    if not get_search_api_url() and not get_web_search_api_url():
        logger.warning("No search backend configured — skipping web search")
        return ""

    config = load_config(env_path)
    client = AsyncOpenAI(
        api_key=config.moderator_llm.api_key,
        base_url=config.moderator_llm.base_url or None,
    )
    model = config.moderator_llm.model

    max_searches = 20
    research_max_results = 20
    collected_info: list[str] = []
    searched_queries: list[str] = []
    seen_commands: set[str] = set()
    seen_urls: set[str] = set()
    no_progress_rounds = 0
    last_feedback: str | None = None
    keyword_searches = 0

    print("🔍 Moderator is researching background information...")

    for i in range(max_searches):
        _t = lambda zh, en: t(topic, zh, en)
        prompt = (
            f"{_t('你是圆桌会议的仲裁者。你需要为即将讨论的议题通过新闻数据库搜索背景信息。', 'You are the moderator of a roundtable meeting. You need to search a news database for background information on the upcoming topic.')}\n\n"
            f"{_t('议题', 'Topic')}：{topic}\n"
        )
        if background:
            prompt += f"{_t('用户提供的背景', 'User-provided background')}：{background}\n"
        if collected_info:
            prompt += f"\n{_t('已搜集到的信息', 'Collected information so far')}：\n{''.join(collected_info[-3:])}\n"
        if searched_queries:
            prompt += f"\n{_t('已执行过的搜索/浏览指令', 'Previously executed search/browse commands')}：{', '.join(searched_queries)}\n"
        if last_feedback:
            prompt += f"\n{_t('上一次搜索反馈', 'Last search feedback')}：{last_feedback}\n"
        prompt += (
            f"\n{_t('请判断是否还需要搜索更多信息。本地搜索不限次数，请尽可能搜索所有你需要的信息。', 'Please decide whether more searching is needed. Local search is unlimited — search as much as you need.')}\n"
            f"{_t('如果信息已经足够充分，回复：DONE', 'If information is sufficient, reply: DONE')}\n"
            f"{_t('如果还需搜索，你只能回复以下五种格式之一：1）一个本地新闻关键词（只能1个词）；2）ALL；3）RECENT:category；4）WEB:查询语句；5）FETCH:url。不要回复句子。', 'If more search is needed, reply in exactly one of these forms: 1) one local-news keyword; 2) ALL; 3) RECENT:category; 4) WEB:search terms; 5) FETCH:url. Do not reply with sentences.')}\n"
            f"{_t('可用 category 例如：china_finance、finance、international、tech、defense、asia、europe、research、middle_east。', 'Available categories include: china_finance, finance, international, tech, defense, asia, europe, research, middle_east.')}\n"
            f"{_t('重要：不要重复任何已经执行过的指令。ALL 或 RECENT:china_finance 这类浏览指令如果已经执行过，就必须换别的 category 或直接回复 DONE。', 'Important: do not repeat any command that has already been executed. If ALL or RECENT:china_finance has already been used, switch to another category or reply DONE.')}\n"
            f"{_t('策略：最近市场新闻优先用本地新闻关键词；股票基本面、东方财富/雪球/公告/券商研报优先用 WEB: 查询；当你已经找到一个高价值网页 URL 时，用 FETCH:url 获取正文。', 'Strategy: use local-news keywords for recent market news; use WEB: for fundamentals, Eastmoney/Xueqiu/announcements/research; when you already have a valuable URL, use FETCH:url to read the page.')}\n"
            f"{_t('默认先做 1-2 次关键词搜索，不要一开始就用 ALL 或 RECENT。本地浏览模式只在本地关键词没有结果或需要市场全景时使用；WEB: 可以在明确需要外部资料时直接使用。', 'Start with 1-2 keyword searches by default. Do not use ALL or RECENT first. Local browse mode should be used only after local keyword misses or for broad market overview; WEB: may be used directly when external information is clearly needed.')}"
        )

        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": t(topic, "你是研究助手。你有五种模式：1）本地新闻关键词搜索：回复1个词；2）浏览全部最新新闻：回复 ALL；3）按分类浏览最新新闻：回复 RECENT:category；4）开放网页搜索：回复 WEB:查询语句；5）网页获取：回复 FETCH:url。系统会把 ALL / RECENT:* 转到本地 /recent，把 WEB: 转到 Tavily 类网页搜索，把 FETCH: 转到网页正文抓取。不要重复指令；如果需要东方财富、雪球、公司公告、财务指标、券商评级等数据库之外的信息，应主动使用 WEB: 或 FETCH:。", "You are a research assistant. You have five modes: 1) one-word local news keyword search; 2) browse all recent news with ALL; 3) browse recent news by category with RECENT:category; 4) open-web search with WEB:query; 5) webpage fetch with FETCH:url. ALL / RECENT:* go to the local /recent API, WEB: goes to open-web search, and FETCH: reads the page body. Do not repeat commands. Use WEB: or FETCH: when you need Eastmoney/Xueqiu/announcements/fundamentals beyond the local database.")},
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

        command = parse_research_command(query)
        backend = command["backend"]
        actual_query = command["query"] or ""
        actual_category = command["category"]
        actual_url = command["url"]
        command_key = command["command_key"] or query

        if backend == "local" and not actual_query and keyword_searches == 0:
            last_feedback = "Please try one keyword search first; do not start with ALL or RECENT."
            print(f"🔍 {last_feedback}")
            continue
        if command_key in seen_commands:
            print(f"🔍 Moderator repeated the same command: {query}. Stopping to avoid fetching the same news repeatedly.")
            break

        searched_queries.append(query)
        seen_commands.add(command_key)
        print(f"🔍 Search #{i + 1}: {query}")

        try:
            if backend == "local":
                result = await tavily_search(
                    query=actual_query,
                    category=actual_category,
                    max_results=research_max_results,
                    topic="news",
                )
                if actual_query:
                    keyword_searches += 1
            elif backend == "web":
                result = await web_search(
                    query=actual_query,
                    max_results=8,
                    search_depth="advanced",
                    topic="general",
                )
            else:
                result = await fetch_webpage_content(actual_url or "")

            result_urls = {r.get("url", "") for r in result.get("results", []) if r.get("url")}
            new_urls = result_urls - seen_urls

            if backend == "local" and actual_query and result.get("result_count", 0) == 0:
                last_feedback = (
                    f"Keyword {query} returned no results. Try another short keyword first; "
                    "if you need information beyond the local database, use WEB: for Eastmoney/Xueqiu/announcements/research."
                )
                print(f"  → Found 0 results")
                print(f"  → {last_feedback}")
                continue

            if new_urls:
                seen_urls.update(new_urls)
                no_progress_rounds = 0
                last_feedback = None
                collected_info.append(result["summary"])
                print(f"  → Found {result['result_count']} results")
            else:
                no_progress_rounds += 1
                last_feedback = (
                    f"Command {query} produced no new news items. Do not repeat it; "
                    "use a different category or reply DONE."
                )
                print(f"  → {last_feedback}")
                if no_progress_rounds >= 2:
                    print("🔍 Two consecutive searches produced no new information. Stopping.")
                    break
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

    # Enable search skill on experts if search is enabled and available
    if search_enabled and skill_search_available():
        for expert in experts:
            expert.search_fn = skill_web_search

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
    from search import get_search_api_url, get_web_search_api_url
    try:
        config = load_config(env_path)
        logger.info("✓ Moderator LLM: %s (%s)", config.moderator_llm.name, config.moderator_llm.model)
        logger.info("✓ Expert providers: %d configured", len(config.expert_providers))
        local_search_url = get_search_api_url()
        web_search_url = get_web_search_api_url()
        if local_search_url:
            logger.info("✓ Local search backend: %s", local_search_url)
        if web_search_url:
            logger.info("✓ Web search backend: %s", web_search_url)
        if not local_search_url and not web_search_url and search_enabled:
            logger.warning("✗ No search backend configured — --search will be skipped")
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
