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
from search import get_tavily_api_key, tavily_search

from openai import AsyncOpenAI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Default expert configs
DEFAULT_EXPERTS = [
    {"name": "创新专家", "role_label": "创新型（偏提出新想法）"},
    {"name": "审慎专家", "role_label": "审慎型（偏发现漏洞）"},
    {"name": "工程专家", "role_label": "工程型（偏落地实现）"},
    {"name": "领域专家", "role_label": "专业型（偏领域知识）"},
]

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
    if get_tavily_api_key():
        logger.info("✓ Tavily API key configured (web search available)")
    else:
        logger.warning("✗ Tavily API key not set — web search disabled")

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
                    await send_event({"type": "error", "content": "请输入讨论议题。"})
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
                    await send_event({"type": "system", "content": "会议已被用户终止。"})
                    await send_event({"type": "meeting_end", "content": "会议已终止。"})

            elif action == "chat":
                user_msg = data.get("message", "").strip()
                if not user_msg:
                    await send_event({"type": "error", "content": "请输入消息。"})
                    continue
                history = _meeting_histories.get(session_id, "")
                if not history:
                    await send_event({"type": "error", "content": "没有可用的会议记录。"})
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

    await send_event({"type": "system", "content": "🔍 仲裁者正在搜索背景信息..."})

    for i in range(MAX_SEARCH_ROUNDS):
        # Ask moderator to generate a search query
        prompt = (
            f"你是圆桌会议的仲裁者。你需要为即将讨论的议题搜索背景信息。\n\n"
            f"议题：{topic}\n"
        )
        if existing_background:
            prompt += f"用户提供的背景：{existing_background}\n"
        if collected_info:
            prompt += f"\n已搜集到的信息：\n{''.join(collected_info[-3:])}\n"
        if searched_queries:
            prompt += f"\n已搜索过的关键词：{', '.join(searched_queries)}\n"

        prompt += (
            f"\n这是第 {i + 1}/{MAX_SEARCH_ROUNDS} 次搜索机会。"
            f"请判断是否还需要搜索更多信息。\n"
            f"如果信息已经足够充分，回复：DONE\n"
            f"如果还需搜索，回复一个简短的搜索关键词（不要回复其他内容，只回复关键词）。"
        )

        try:
            resp = await moderator_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是圆桌会议的研究助手。根据议题判断是否需要搜索，并生成精准的搜索关键词。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            query = (resp.choices[0].message.content or "").strip()
        except Exception as e:
            logger.warning("Research LLM call failed: %s", e)
            break

        if not query or query.upper() == "DONE":
            await send_event({"type": "system", "content": "🔍 仲裁者判断信息已充分，结束搜索。"})
            break

        searched_queries.append(query)
        await send_event({"type": "search_start", "content": f"🔍 搜索 ({i + 1}/{MAX_SEARCH_ROUNDS}): {query}"})

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
            await send_event({"type": "system", "content": f"搜索失败: {e}"})

    if collected_info:
        await send_event({"type": "system", "content": f"🔍 搜索完成，共进行了 {len(searched_queries)} 次搜索。开始圆桌会议..."})
    else:
        await send_event({"type": "system", "content": "🔍 未搜集到有效信息，直接开始圆桌会议..."})

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
            for cfg in DEFAULT_EXPERTS
        ]

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
                meeting.opening_text or ""
            )

    except asyncio.CancelledError:
        await send_event({"type": "system", "content": "会议已取消。"})
        await send_event({"type": "meeting_end", "content": "会议已终止。"})
    except Exception as e:
        logger.exception("Meeting failed for session %s", session_id)
        await send_event({"type": "error", "content": f"会议出错: {e}"})
        await send_event({"type": "meeting_end", "content": "会议异常结束。"})
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
            "你是刚刚结束的圆桌会议的仲裁者。以下是完整的会议讨论记录。\n"
            "用户现在对会议内容有后续问题，请基于会议记录回答。\n"
            "保持中立、准确、简洁。如果用户问到会议中没有讨论的内容，请如实说明。\n\n"
            f"=== 会议记录 ===\n{meeting_history}\n=== 会议记录结束 ==="
        )

        # Initialize or retrieve session chat history
        if session_id not in _chat_histories:
            _chat_histories[session_id] = []

        _chat_histories[session_id].append({"role": "user", "content": user_message})

        messages = [{"role": "system", "content": system_prompt}] + _chat_histories[session_id]

        await send_event({"type": "agent_start", "agent": "Moderator", "role": "仲裁者", "round": "chat"})

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
            "role": "仲裁者",
            "round": "chat",
            "content": reply,
        })

    except Exception as e:
        logger.exception("Chat with moderator failed for session %s", session_id)
        await send_event({"type": "error", "content": f"对话出错: {e}"})


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
