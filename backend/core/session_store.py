import json
from copy import deepcopy
from threading import Lock

from redis import Redis
from redis.exceptions import RedisError

from core.config import REDIS_SESSION_TTL, REDIS_URL


_DEFAULT_SESSION = {
    "profile": {
        "user_name": None,
        "age": None,
        "favorite_subjects": [],
        "activity_type": None,
        "interests": [],
        "career_goal": None,
        "ent_score": None,
        "language": None,
        "budget": None,
    },
    "facts": [],
    "messages": [],
    "memory": {"last_summarized_index": 0, "summaries": []},
    "active_flow": None,
}

_redis = Redis.from_url(REDIS_URL, decode_responses=True)
_memory_sessions: dict[str, dict] = {}
_memory_lock = Lock()


def _normalize_session(session: dict) -> dict:
    merged = deepcopy(_DEFAULT_SESSION)
    if isinstance(session, dict):
        for key, value in session.items():
            if key == "profile" and isinstance(value, dict):
                merged["profile"].update(value)
            else:
                merged[key] = value
    merged.setdefault("messages", [])
    merged.setdefault("facts", [])
    merged.setdefault("memory", {"last_summarized_index": 0, "summaries": []})
    if not isinstance(merged.get("memory"), dict):
        merged["memory"] = {"last_summarized_index": 0, "summaries": []}
    merged["memory"].setdefault("last_summarized_index", 0)
    merged["memory"].setdefault("summaries", [])
    return merged


def _session_key(session_id: str) -> str:
    return f"assistant:session:{session_id}"


def _memory_get_session(session_id: str) -> dict:
    with _memory_lock:
        session = _memory_sessions.setdefault(session_id, deepcopy(_DEFAULT_SESSION))
        return _normalize_session(session)


def _memory_save_session(session_id: str, session: dict) -> None:
    with _memory_lock:
        _memory_sessions[session_id] = _normalize_session(session)


def get_session(session_id: str) -> dict:
    try:
        raw = _redis.get(_session_key(session_id))
    except RedisError:
        return _memory_get_session(session_id)
    if raw is None:
        return _normalize_session({})
    return _normalize_session(json.loads(raw))


def save_session(session_id: str, session: dict) -> None:
    normalized = _normalize_session(session)
    try:
        _redis.set(
            _session_key(session_id),
            json.dumps(normalized, ensure_ascii=False),
            ex=REDIS_SESSION_TTL,
        )
    except RedisError:
        _memory_save_session(session_id, normalized)
