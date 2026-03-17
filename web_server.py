"""FastAPI web server with WebSocket for the Roundtable Meeting System."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from config import MeetingConfig, load_config
from models import MeetingInput
from agents import ModeratorAgent, ExpertAgent
from meeting_streaming import StreamingMeeting
from i18n import t, get_default_experts, is_chinese, LANG_FOLLOW_INSTRUCTION
from search import (
    fetch_webpage_content,
    get_search_api_url,
    get_web_search_api_url,
    parse_research_command,
    tavily_search,
    web_search,
)
from skills.web_search import execute as skill_web_search, is_available as skill_search_available

from openai import AsyncOpenAI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Default expert configs
DEFAULT_EXPERTS = get_default_experts  # function; called with topic at runtime

# Active meetings: session_id -> StreamingMeeting
_active_meetings: dict[str, StreamingMeeting] = {}
# Completed meeting history: session_id -> full discussion text
_meeting_histories: dict[str, str] = {}


def _validate_startup_configuration(target_app: FastAPI) -> MeetingConfig:
    """Validate app configuration and cache it on the FastAPI app state."""
    config = load_config()
    target_app.state.meeting_config = config

    if getattr(target_app.state, "config_validated", False):
        return config

    logger.info("✓ Moderator LLM: %s (%s)", config.moderator_llm.name, config.moderator_llm.model)
    logger.info("✓ Expert providers: %d configured", len(config.expert_providers))
    for i, provider in enumerate(config.expert_providers, 1):
        logger.info("  Provider %d: %s (%s) weight=%d", i, provider.name, provider.model, provider.weight)
    local_search_url = get_search_api_url()
    web_search_url = get_web_search_api_url()
    if local_search_url:
        logger.info("✓ Local search backend: %s", local_search_url)
    if web_search_url:
        logger.info("✓ Web search backend: %s", web_search_url)
    if not local_search_url and not web_search_url:
        logger.warning("✗ No search backend configured — web search disabled")

    target_app.state.config_validated = True
    return config


def _get_runtime_config() -> MeetingConfig:
    config = getattr(app.state, "meeting_config", None)
    if config is None:
        config = _validate_startup_configuration(app)
    return config


@asynccontextmanager
async def lifespan(target_app: FastAPI):
    try:
        _validate_startup_configuration(target_app)
    except (ValueError, FileNotFoundError) as e:
        logger.error("Configuration error: %s", e)
        logger.error("First run: copy .env.example to .env and fill in the required keys.")
        raise RuntimeError("Invalid application configuration.") from e
    yield


app = FastAPI(title="Roundtable Meeting System", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    logger.info("WebSocket connected: session=%s", session_id)

    async def send_event(event: dict[str, Any]):
        try:
            await websocket.send_json(event)
        except Exception:
            logger.warning("Failed to send event to session %s", session_id)

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")

            if action == "start_meeting":
                topic = data.get("topic", "").strip()
                if not topic:
                    await send_event({"type": "error", "content": "Please enter a discussion topic. / 请输入讨论议题。"})
                    continue

                goal = data.get("goal", "").strip() or None
                background = data.get("background", "").strip() or None
                constraints_raw = data.get("constraints", "").strip()
                constraints = [c.strip() for c in constraints_raw.split("\n") if c.strip()] if constraints_raw else []
                search_enabled = data.get("search_enabled", False)

                # Cancel any existing meeting for this session
                if session_id in _active_meetings:
                    _active_meetings[session_id].cancel()

                # Start meeting in background
                asyncio.create_task(
                    _run_meeting_task(session_id, topic, goal, constraints, background, search_enabled, send_event)
                )

            elif action == "stop_meeting":
                if session_id in _active_meetings:
                    _active_meetings[session_id].cancel()
                    await send_event({"type": "system", "content": t(topic, "会议已被用户终止。", "Meeting stopped by user.")})
                    await send_event({"type": "meeting_end", "content": t(topic, "会议已终止。", "Meeting terminated.")})

            elif action == "chat":
                user_msg = data.get("message", "").strip()
                if not user_msg:
                    await send_event({"type": "error", "content": "Please enter a message. / 请输入消息。"})
                    continue
                history = _meeting_histories.get(session_id, "")
                if not history:
                    await send_event({"type": "error", "content": "No meeting history available. / 没有可用的会议记录。"})
                    continue
                asyncio.create_task(
                    _chat_with_moderator(session_id, user_msg, history, send_event)
                )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: session=%s", session_id)
        if session_id in _active_meetings:
            _active_meetings[session_id].cancel()
            del _active_meetings[session_id]
        # Keep _meeting_histories — it persists for reconnections


MAX_SEARCH_ROUNDS = 20
RESEARCH_MAX_RESULTS = 20


async def _research_phase(
    topic: str,
    existing_background: str | None,
    send_event,
) -> str:
    """Moderator researches the topic using Tavily, up to MAX_SEARCH_ROUNDS searches."""
    config = _get_runtime_config()
    moderator_client = AsyncOpenAI(
        api_key=config.moderator_llm.api_key,
        base_url=config.moderator_llm.base_url or None,
    )
    model = config.moderator_llm.model

    collected_info: list[str] = []
    searched_queries: list[str] = []
    seen_commands: set[str] = set()
    seen_urls: set[str] = set()
    no_progress_rounds = 0
    last_feedback: str | None = None
    keyword_searches = 0

    await send_event({"type": "system", "content": t(topic, "🔍 仲裁者正在搜索背景信息...", "🔍 Moderator is searching for background information...")})

    for i in range(MAX_SEARCH_ROUNDS):
        # Ask moderator to generate a search query
        _t = lambda zh, en: t(topic, zh, en)
        prompt = (
            f"{_t('你是圆桌会议的仲裁者。你需要为即将讨论的议题通过新闻数据库搜索背景信息。', 'You are the moderator of a roundtable meeting. You need to search a news database for background information on the upcoming topic.')}\n\n"
            f"{_t('议题', 'Topic')}：{topic}\n"
        )
        if existing_background:
            prompt += f"{_t('用户提供的背景', 'User-provided background')}：{existing_background}\n"
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
            resp = await moderator_client.chat.completions.create(
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
            await send_event({"type": "system", "content": t(topic, "🔍 仲裁者判断信息已充分，结束搜索。", "🔍 Moderator determined information is sufficient.")})
            break

        command = parse_research_command(query)
        backend = command["backend"]
        actual_query = command["query"] or ""
        actual_category = command["category"]
        actual_url = command["url"]
        command_key = command["command_key"] or query

        if backend == "local" and not actual_query and keyword_searches == 0:
            last_feedback = t(topic, "请先尝试一个关键词搜索，不要一开始就使用 ALL 或 RECENT。", "Please try one keyword search first; do not start with ALL or RECENT.")
            await send_event({"type": "system", "content": f"🔍 {last_feedback}"})
            continue
        if command_key in seen_commands:
            await send_event({"type": "system", "content": t(topic, f"🔍 仲裁者重复了相同搜索指令：{query}。为避免重复抓取相同新闻，结束搜索。", f"🔍 Moderator repeated the same command: {query}. Stopping to avoid fetching the same news repeatedly.")})
            break

        searched_queries.append(query)
        seen_commands.add(command_key)
        await send_event({"type": "search_start", "content": f"🔍 {t(topic, '搜索', 'Search')} #{i + 1}: {query}"})

        try:
            if backend == "local":
                result = await tavily_search(
                    query=actual_query,
                    category=actual_category,
                    max_results=RESEARCH_MAX_RESULTS,
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
                last_feedback = t(
                    topic,
                    f"关键词 {query} 没有搜索到结果。可先换一个更短的关键词；如果需要数据库外的信息，也可以改用 WEB: 查询东方财富、雪球、公告、研报。",
                    f"Keyword {query} returned no results. Try another short keyword first; if you need information beyond the local database, use WEB: for Eastmoney/Xueqiu/announcements/research.",
                )
                await send_event({
                    "type": "search_result",
                    "content": result["summary"],
                    "result_count": result["result_count"],
                })
                await send_event({"type": "system", "content": f"🔍 {last_feedback}"})
                continue

            if new_urls:
                seen_urls.update(new_urls)
                no_progress_rounds = 0
                last_feedback = None
                collected_info.append(result["summary"])
                await send_event({
                    "type": "search_result",
                    "content": result["summary"],
                    "result_count": result["result_count"],
                })
            else:
                no_progress_rounds += 1
                last_feedback = t(
                    topic,
                    f"指令 {query} 没有带来新增新闻。不要重复它；如果还要继续，请改用别的 category 或直接 DONE。",
                    f"Command {query} produced no new news items. Do not repeat it; use a different category or reply DONE.",
                )
                await send_event({"type": "system", "content": f"🔍 {last_feedback}"})
                if no_progress_rounds >= 2:
                    await send_event({"type": "system", "content": t(topic, "🔍 连续两次没有新增信息，结束搜索。", "🔍 Two consecutive searches produced no new information. Stopping.")})
                    break
        except Exception as e:
            logger.warning("Search failed for query '%s': %s", query, e)
            await send_event({"type": "system", "content": t(topic, f"搜索失败: {e}", f"Search failed: {e}")})

    if collected_info:
        await send_event({"type": "system", "content": t(topic, f"🔍 搜索完成，共进行了 {len(searched_queries)} 次搜索。开始圆桌会议...", f"🔍 Search complete ({len(searched_queries)} searches). Starting roundtable meeting...")})
    else:
        await send_event({"type": "system", "content": t(topic, "🔍 未搜集到有效信息，直接开始圆桌会议...", "🔍 No useful information found, starting roundtable meeting directly...")})

    return "\n\n".join(collected_info)


async def _run_meeting_task(
    session_id: str,
    topic: str,
    goal: str | None,
    constraints: list[str],
    background: str | None,
    search_enabled: bool,
    send_event,
):
    try:
        config = _get_runtime_config()

        # === Research phase: moderator auto-searches if search skill is enabled ===
        search_background = ""
        if search_enabled:
            search_background = await _research_phase(topic, background, send_event)

        # Combine user-provided background with search results
        combined_background = ""
        if background:
            combined_background = background
        if search_background:
            combined_background = (combined_background + "\n\n" if combined_background else "") + search_background

        meeting_input = MeetingInput(
            topic=topic, goal=goal, constraints=constraints,
            background=combined_background or None,
        )

        moderator = ModeratorAgent(provider=config.moderator_llm)

        experts = [
            ExpertAgent(
                name=cfg["name"],
                role_label=cfg["role_label"],
                provider=config.select_expert_provider(),
            )
            for cfg in DEFAULT_EXPERTS(topic)
        ]

        # Enable search skill on experts if a search backend is configured
        if search_enabled and skill_search_available():
            for expert in experts:
                expert.search_fn = skill_web_search

        meeting = StreamingMeeting(
            moderator=moderator,
            experts=experts,
            max_rounds=4,
            on_event=send_event,
        )
        _active_meetings[session_id] = meeting

        final_report = await meeting.run(meeting_input)

        # Save the full discussion history for post-meeting chat
        if final_report:
            _meeting_histories[session_id] = meeting._build_full_discussion(
                meeting.opening_text or "", topic
            )

    except asyncio.CancelledError:
        await send_event({"type": "system", "content": t(topic, "会议已取消。", "Meeting cancelled.")})
        await send_event({"type": "meeting_end", "content": t(topic, "会议已终止。", "Meeting terminated.")})
    except Exception as e:
        logger.exception("Meeting failed for session %s", session_id)
        await send_event({"type": "error", "content": t(topic, f"会议出错: {e}", f"Meeting error: {e}")})
        await send_event({"type": "meeting_end", "content": t(topic, "会议异常结束。", "Meeting ended abnormally.")})
    finally:
        _active_meetings.pop(session_id, None)


# Per-session chat history for post-meeting follow-up
_chat_histories: dict[str, list[dict[str, str]]] = {}


async def _chat_with_moderator(
    session_id: str,
    user_message: str,
    meeting_history: str,
    send_event,
):
    """After a meeting ends, let the moderator chat with the user based on meeting history."""
    try:
        config = _get_runtime_config()
        client = AsyncOpenAI(
            api_key=config.moderator_llm.api_key,
            base_url=config.moderator_llm.base_url or None,
        )

        system_prompt = (
            t(user_message,
              "你是刚刚结束的圆桌会议的仲裁者。以下是完整的会议讨论记录。\n"
              "用户现在对会议内容有后续问题，请基于会议记录回答。\n"
              "保持中立、准确、简洁。如果用户问到会议中没有讨论的内容，请如实说明。\n\n",
              "You are the moderator of a roundtable meeting that just ended. Below is the full meeting transcript.\n"
              "The user has follow-up questions about the meeting. Answer based on the transcript.\n"
              "Stay neutral, accurate, and concise. If the user asks about something not discussed, say so.\n\n"
            )
            + f"=== Meeting Record ===\n{meeting_history}\n=== End of Record ==="
            + LANG_FOLLOW_INSTRUCTION
        )

        # Initialize or retrieve session chat history
        if session_id not in _chat_histories:
            _chat_histories[session_id] = []

        _chat_histories[session_id].append({"role": "user", "content": user_message})

        messages = [{"role": "system", "content": system_prompt}] + _chat_histories[session_id]

        await send_event({"type": "agent_start", "agent": "Moderator", "role": t(user_message, "仲裁者", "Arbiter"), "round": "chat"})

        resp = await client.chat.completions.create(
            model=config.moderator_llm.model,
            messages=messages,
            temperature=0.5,
        )
        reply = resp.choices[0].message.content or ""

        _chat_histories[session_id].append({"role": "assistant", "content": reply})

        await send_event({
            "type": "agent_message",
            "agent": "Moderator",
            "role": t(user_message, "仲裁者", "Arbiter"),
            "round": "chat",
            "content": reply,
        })

    except Exception as e:
        logger.exception("Chat with moderator failed for session %s", session_id)
        await send_event({"type": "error", "content": t(user_message, f"对话出错: {e}", f"Chat error: {e}")})


if __name__ == "__main__":
    import uvicorn

    try:
        _validate_startup_configuration(app)
    except (ValueError, FileNotFoundError) as e:
        logger.error("Configuration error: %s", e)
        logger.error("First run: copy .env.example to .env and fill in the required keys.")
        import sys
        sys.exit(1)

    uvicorn.run(app, host="0.0.0.0", port=3088)
