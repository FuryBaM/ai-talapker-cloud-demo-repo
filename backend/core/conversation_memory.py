from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

from core.config import EMBED_BATCH_SIZE, STORAGE_DIR, TOKEN_LIMIT
from core.model_store import embed_model, embed_tokenizer
from core.rag import _clear_embedding_cache, _encode_texts, make_query, truncate_for_embedding


MEMORY_ENABLED = os.getenv("APP_MEMORY_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
SUMMARY_EVERY_N = max(2, int(os.getenv("APP_MEMORY_SUMMARY_EVERY_N", "8")))
RECENT_RAW_LIMIT = max(2, int(os.getenv("APP_MEMORY_RECENT_RAW_LIMIT", "12")))
MEMORY_TOP_K = max(0, int(os.getenv("APP_MEMORY_TOP_K", "4")))
MEMORY_MIN_SCORE = float(os.getenv("APP_MEMORY_MIN_SCORE", "0.22"))
MEMORY_SUMMARY_MAX_TOKENS = max(80, int(os.getenv("APP_MEMORY_SUMMARY_MAX_TOKENS", "260")))
MEMORY_STORE_PATH = Path(os.getenv("APP_MEMORY_STORE_PATH", str(STORAGE_DIR / "session_memory_chunks.jsonl")))

_STORE_LOCK = Lock()


def _now_ms() -> int:
    return int(time.time() * 1000)


def normalize_role(role: str) -> str:
    value = str(role or "user").strip().lower()
    if value in {"ai", "assistant", "bot"}:
        return "assistant"
    if value in {"system"}:
        return "system"
    return "user"


def _clean_text(value: Any, limit: int | None = None) -> str:
    text = str(value or "").replace("\r", " ").strip()
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def ensure_memory_state(session: dict) -> dict:
    memory = session.setdefault("memory", {})
    memory.setdefault("last_summarized_index", 0)
    memory.setdefault("summaries", [])
    return memory


def append_raw_message(
    session: dict,
    *,
    role: str,
    content: str,
    message_id: str | None = None,
    reply_to: dict | None = None,
) -> dict:
    messages = session.setdefault("messages", [])
    item = {
        "id": message_id or f"srv-{uuid.uuid4().hex}",
        "role": normalize_role(role),
        "content": _clean_text(content),
        "ts": _now_ms(),
    }
    if reply_to:
        item["reply_to"] = sanitize_reply_reference(reply_to)
    messages.append(item)
    max_raw_messages = int(os.getenv("APP_MEMORY_MAX_RAW_MESSAGES", "120"))
    if max_raw_messages > 0 and len(messages) > max_raw_messages:
        del messages[: len(messages) - max_raw_messages]
        memory = ensure_memory_state(session)
        memory["last_summarized_index"] = min(int(memory.get("last_summarized_index") or 0), len(messages))
    return item


def sanitize_reply_reference(reply_to: dict | Any | None) -> dict | None:
    if not reply_to:
        return None
    if hasattr(reply_to, "model_dump"):
        reply_to = reply_to.model_dump()
    if not isinstance(reply_to, dict):
        return None
    content = _clean_text(reply_to.get("content"), limit=1200)
    if not content:
        return None
    return {
        "id": str(reply_to.get("id") or ""),
        "role": normalize_role(str(reply_to.get("role") or "user")),
        "content": content,
        "ts": reply_to.get("ts"),
    }


def format_messages(messages: list[dict], *, limit: int | None = None, max_chars: int = 6000) -> str:
    selected = messages[-limit:] if limit else list(messages)
    lines: list[str] = []
    for message in selected:
        role = normalize_role(str(message.get("role") or "user"))
        label = "AI" if role == "assistant" else "User"
        content = _clean_text(message.get("content"), limit=1000)
        if not content:
            continue
        reply = sanitize_reply_reference(message.get("reply_to"))
        if reply:
            reply_label = "AI" if reply["role"] == "assistant" else "User"
            lines.append(f"{label} replied to {reply_label}: {_clean_text(reply['content'], limit=240)}")
        lines.append(f"{label}: {content}")
    text = "\n".join(lines).strip()
    if len(text) > max_chars:
        return text[-max_chars:].lstrip()
    return text


def recent_raw_history(session: dict, *, limit: int | None = None) -> str:
    return format_messages(session.get("messages", []), limit=limit or RECENT_RAW_LIMIT)


def reply_reference_text(reply_to: dict | Any | None) -> str:
    reply = sanitize_reply_reference(reply_to)
    if not reply:
        return ""
    label = "AI" if reply["role"] == "assistant" else "User"
    return f"Selected reply_to message ({label}): {reply['content']}"


def _read_store() -> list[dict]:
    if not MEMORY_STORE_PATH.exists():
        return []
    rows: list[dict] = []
    with MEMORY_STORE_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _write_store(rows: list[dict]) -> None:
    MEMORY_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MEMORY_STORE_PATH.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _upsert_memory_chunk(row: dict) -> None:
    with _STORE_LOCK:
        rows = _read_store()
        chunk_id = row.get("chunk_id")
        rows = [item for item in rows if item.get("chunk_id") != chunk_id]
        rows.append(row)
        max_chunks = int(os.getenv("APP_MEMORY_MAX_CHUNKS", "1000"))
        if max_chunks > 0 and len(rows) > max_chunks:
            rows = rows[-max_chunks:]
        _write_store(rows)


def _embed_passage(text: str) -> list[float]:
    clipped = truncate_for_embedding(text, tokenizer=embed_tokenizer, limit=TOKEN_LIMIT)
    vector = _encode_texts(
        embed_model,
        [f"passage: {clipped}"],
        batch_size=max(1, int(EMBED_BATCH_SIZE or 1)),
    )[0]
    _clear_embedding_cache()
    return vector.astype(np.float32).tolist()


def _fallback_summary(batch: list[dict]) -> str:
    return format_messages(batch, limit=None, max_chars=3200)


def _llm_summary(batch: list[dict], *, lang: str, max_new_tokens: int) -> str:
    from core.generation import generate_from_messages

    transcript = format_messages(batch, limit=None, max_chars=6000)
    if not transcript:
        return ""
    messages = [
        {
            "role": "system",
            "content": (
                "You compress chat history for a university admissions assistant. "
                "Write a semantic memory block, not a dialogue transcript. "
                "Keep durable user profile facts, preferences, constraints, goals, decisions, and unresolved follow-ups. "
                "Do not invent facts. Do not include official university policy unless the user personally relied on it. "
                "Use compact bullet points."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Language hint: {lang}\n\n"
                f"Transcript block:\n{transcript}\n\n"
                "Return only the memory block."
            ),
        },
    ]
    return _clean_text(generate_from_messages(messages, max_new_tokens=max_new_tokens, ctx_texts=None), limit=3000)


def _build_memory_chunk_text(summary: str, batch: list[dict], *, block_index: int) -> str:
    raw_tail = format_messages(batch, limit=6, max_chars=2200)
    return (
        f"Conversation memory block {block_index}\n"
        f"Summary:\n{summary.strip()}\n\n"
        f"Raw evidence excerpt:\n{raw_tail}"
    ).strip()


def maybe_update_memory(session_id: str, session: dict, *, use_llm: bool = True, lang: str = "ru") -> list[dict]:
    if not MEMORY_ENABLED:
        return []
    memory = ensure_memory_state(session)
    messages = session.get("messages", [])
    last = int(memory.get("last_summarized_index") or 0)
    created: list[dict] = []
    while len(messages) - last >= SUMMARY_EVERY_N:
        batch = messages[last : last + SUMMARY_EVERY_N]
        block_index = len(memory.get("summaries", [])) + 1
        try:
            summary = _llm_summary(batch, lang=lang, max_new_tokens=MEMORY_SUMMARY_MAX_TOKENS) if use_llm else _fallback_summary(batch)
        except Exception:
            summary = _fallback_summary(batch)
        summary = _clean_text(summary, limit=3000)
        if not summary:
            last += SUMMARY_EVERY_N
            memory["last_summarized_index"] = last
            continue
        chunk_id = f"{session_id}:memory:{block_index}"
        text = _build_memory_chunk_text(summary, batch, block_index=block_index)
        row = {
            "chunk_id": chunk_id,
            "session_id": session_id,
            "block_index": block_index,
            "from_index": last,
            "to_index": last + len(batch) - 1,
            "created_at": _now_ms(),
            "summary": summary,
            "text": text,
        }
        try:
            row["embedding"] = _embed_passage(text)
            _upsert_memory_chunk(row)
        except Exception as exc:
            row["embedding_error"] = str(exc)
        memory.setdefault("summaries", []).append({k: row[k] for k in row if k != "embedding"})
        created.append(row)
        last += SUMMARY_EVERY_N
        memory["last_summarized_index"] = last
    return created


def search_memory_chunks(session_id: str, query: str, *, top_k: int | None = None) -> list[dict]:
    if not MEMORY_ENABLED:
        return []
    safe_top_k = MEMORY_TOP_K if top_k is None else max(0, int(top_k))
    if safe_top_k <= 0:
        return []
    query = _clean_text(query, limit=2000)
    if not query:
        return []
    with _STORE_LOCK:
        rows = [row for row in _read_store() if row.get("session_id") == session_id and row.get("embedding")]
    if not rows:
        return []
    try:
        q = make_query(query).astype(np.float32)
    except Exception:
        return []
    scored: list[dict] = []
    for row in rows:
        try:
            vector = np.array(row.get("embedding") or [], dtype=np.float32)
            if not vector.size or vector.shape[0] != q.shape[0]:
                continue
            score = float(np.dot(q, vector))
        except Exception:
            continue
        if score < MEMORY_MIN_SCORE:
            continue
        copy = {k: v for k, v in row.items() if k != "embedding"}
        copy["score"] = score
        scored.append(copy)
    scored.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    return scored[:safe_top_k]


def format_memory_chunks(chunks: list[dict], *, max_chars: int = 4500) -> str:
    lines: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        score = chunk.get("score")
        score_text = f" score={score:.3f}" if isinstance(score, float) else ""
        text = _clean_text(chunk.get("summary") or chunk.get("text"), limit=1200)
        if text:
            lines.append(f"Memory chunk {index}{score_text}:\n{text}")
    joined = "\n\n".join(lines).strip()
    if len(joined) > max_chars:
        return joined[:max_chars].rstrip() + "…"
    return joined


def profile_context(profile: dict | None) -> str:
    if not isinstance(profile, dict):
        return ""
    lines: list[str] = []
    for key, value in profile.items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value if item)
        else:
            rendered = str(value)
        if rendered:
            lines.append(f"{key}: {rendered}")
    return "\n".join(lines)


def build_turn_memory_context(
    *,
    session_id: str,
    session: dict,
    query: str,
    reply_to: dict | Any | None = None,
    top_k: int | None = None,
) -> dict:
    reply = sanitize_reply_reference(reply_to)
    expanded_query = query
    if reply:
        expanded_query = f"{query}\n\nReply target:\n{reply['content']}"
    chunks = search_memory_chunks(session_id, expanded_query, top_k=top_k)
    return {
        "recent_raw_messages": recent_raw_history(session, limit=RECENT_RAW_LIMIT),
        "reply_to": reply,
        "reply_to_text": reply_reference_text(reply),
        "relevant_memory_chunks": chunks,
        "relevant_memory_text": format_memory_chunks(chunks),
        "profile_text": profile_context(session.get("profile", {})),
    }


def combined_memory_prompt_block(memory_context: dict, *, include_recent_raw: bool = True) -> str:
    parts: list[str] = []
    profile = _clean_text(memory_context.get("profile_text"))
    if profile:
        parts.append("User profile:\n" + profile)
    reply = _clean_text(memory_context.get("reply_to_text"))
    if reply:
        parts.append(reply)
    relevant = _clean_text(memory_context.get("relevant_memory_text"))
    if relevant:
        parts.append("Relevant long-term session memory:\n" + relevant)
    if include_recent_raw:
        recent = _clean_text(memory_context.get("recent_raw_messages"))
        if recent:
            parts.append("Recent raw messages:\n" + recent)
    return "\n\n".join(parts).strip()
