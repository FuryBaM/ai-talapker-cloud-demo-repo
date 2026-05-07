from uuid import uuid4

from core.agent_state import AgentState
from core.config import ALLOW_WEB_SEARCH_DEFAULT
from core.conversation_memory import (
    append_raw_message,
    build_turn_memory_context,
    combined_memory_prompt_block,
    maybe_update_memory,
    sanitize_reply_reference,
)
from core.graph import AGENT_GRAPH
from core.session_store import get_session, save_session


def run_agent_turn(
    message: str,
    lang: str = "ru",
    use_llm: bool = True,
    session_id: str | None = None,
    allow_web_search: bool = ALLOW_WEB_SEARCH_DEFAULT,
    reply_to: dict | None = None,
    message_id: str | None = None,
) -> AgentState:
    current_session_id = session_id or str(uuid4())
    session = get_session(current_session_id)
    reply = sanitize_reply_reference(reply_to)
    append_raw_message(session, role="user", content=message, message_id=message_id, reply_to=reply)

    memory_context = build_turn_memory_context(
        session_id=current_session_id,
        session=session,
        query=message,
        reply_to=reply,
    )
    memory_prompt = combined_memory_prompt_block(memory_context, include_recent_raw=True)

    state: AgentState = {
        "session_id": current_session_id,
        "lang": lang,
        "user_message": message,
        "use_llm": use_llm,
        "allow_web_search": allow_web_search,
        "reply_to": reply,
        "recent_raw_messages": memory_context.get("recent_raw_messages", ""),
        "relevant_memory_chunks": memory_context.get("relevant_memory_chunks", []),
        "memory_context": memory_context,
        "memory_prompt": memory_prompt,
        "session": session,
        "profile": session["profile"],
        "route": "knowledge",
        "profile_complete": False,
    }
    result = AGENT_GRAPH.invoke(state)
    result_session = result["session"]
    maybe_update_memory(current_session_id, result_session, use_llm=use_llm, lang=lang)
    save_session(current_session_id, result_session)
    result["session_id"] = current_session_id
    result["session"] = result_session
    return result
