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
from search import get_search_api_url, tavily_search
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
    search_url = get_search_api_url()
    if search_url:
        logger.info("✓ Search backend: %s", search_url)
    else:
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


MAX_SEARCH_ROUNDS = 5


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
            prompt += f"\n{_t('已搜索过的关键词', 'Previously searched keywords')}：{', '.join(searched_queries)}\n"

        prompt += (
            f"\n{_t(f'这是第 {i + 1}/{MAX_SEARCH_ROUNDS} 次搜索机会。', f'This is search opportunity {i + 1}/{MAX_SEARCH_ROUNDS}.')}"
            f"{_t('请判断是否还需要搜索更多信息。', 'Please decide whether more searching is needed.')}\n"
            f"{_t('如果信息已经足够充分，回复：DONE', 'If information is sufficient, reply: DONE')}\n"
            f"{_t('如果还需搜索，回复一个精简的搜索关键词（1-3个词，像查数据库一样简短，例如：A股、港股、芯片、关税、新能源）。不要回复其他内容，不要写完整句子。', 'If more search is needed, reply with 1-3 short keywords (like a database search, e.g.: stocks, tariffs, chips, oil). No other text, no full sentences.')}"
        )

        try:
            resp = await moderator_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": t(topic, "你是新闻数据库搜索助手。你的任务是将议题拆解为多个简短搜索词（每次1-3个词），逐次搜索新闻数据库。注意：这不是谷歌搜索引擎，不能输入长句子，必须用精简关键词。每次只回复一个搜索词或DONE。", "You are a news database search assistant. Break topics into short keywords (1-3 words each) for sequential database searches. This is NOT a web search engine — long sentences won't work. Reply with one short keyword or DONE.")},
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

        searched_queries.append(query)
        await send_event({"type": "search_start", "content": f"🔍 {t(topic, '搜索', 'Search')} ({i + 1}/{MAX_SEARCH_ROUNDS}): {query}"})

        try:
            result = await tavily_search(query=query, max_results=5, topic="news")
            collected_info.append(result["summary"])
            await send_event({
                "type": "search_result",
                "content": result["summary"],
                "result_count": result["result_count"],
            })
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
