import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path

import uvicorn
from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from core.admin_auth import (
    authenticate_admin,
    create_admin_user,
    create_api_key,
    list_admin_users,
    list_api_keys,
    list_audit_log,
    permissions_payload,
    require_admin,
    require_permission,
    revoke_api_key,
    update_admin_user,
)
from core.config import INPUT_DATA_DIR, KNOWLEDGE_ENTRIES_PATH, KNOWLEDGE_REGISTRY_PATH, RAG_CHUNKS_PATH, QDRANT_COLLECTION
from core.entry_store import delete_curated_entry, load_curated_entries, upsert_curated_entry
from core.generation import fallback_suggestions, generate_from_messages, generate_session_suggestions, generate_suggestions
from core.interview_service import refresh_interview_metadata
from core.knowledge_assets import (
    get_index,
    reload_knowledge_assets,
    reindex_curated_entry,
    reindex_source,
    shutdown_knowledge_assets,
)
from core.knowledge_catalog import load_catalog, upsert_catalog
from core.knowledge_registry import load_registry, upsert_registry
from core.normalize_input_data import parse_source_content
from core.ocr_ingest import OCR_INPUT_EXTENSIONS, process_ocr_upload
from core.orchestrator import run_agent_turn
from core.rag import QdrantIndex, _make_qdrant_client, search_debug
from core.security import (
    DEFAULT_MAX_UPLOAD_BYTES,
    safe_child_path,
    safe_filename,
    safe_slug,
    save_upload_file_limited,
    unique_child_path,
)
from core.training_dataset_service import (
    add_dataset_items,
    available_training_models,
    create_dataset,
    create_training_job,
    delete_dataset_item,
    export_dataset,
    get_dataset,
    list_datasets,
    list_training_jobs,
    start_training_job,
    stop_training_job,
    suggest_dataset_items,
    update_dataset_item,
)
from core.schemas import (
    Answer,
    AdminLoginRequest,
    AdminLoginResponse,
    Ask,
    CatalogResponse,
    ChatRequest,
    ChatResponse,
    CuratedEntry,
    KnowledgeCatalog,
    KnowledgeRegistrySource,
    KnowledgeSourceItem,
    EntryFormat,
    _entry_format_from_legacy,
    KnowledgeEntry,
    KnowledgeEntryPreviewResponse,
    RagRebuildRequest,
    RagRebuildResponse,
    RegistryResponse,
    RegistryUpdateRequest,
    SearchDebugHit,
    SearchDebugRequest,
    SearchDebugResponse,
    SourceMappingUpdateRequest,
    SourcePreviewResponse,
    SuggestRequest,
    SuggestResponse,
)

app = FastAPI()


def _cors_origins() -> list[str]:
    raw = os.getenv(
        "APP_CORS_ORIGINS",
        "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8000,http://localhost:8000",
    ).strip()
    if raw == "*":
        if os.getenv("APP_ALLOW_WILDCARD_CORS", "0") != "1":
            return ["http://127.0.0.1:5500", "http://localhost:5500", "http://127.0.0.1:8000", "http://localhost:8000"]
        return ["*"]
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


def _cors_origin_regex() -> str | None:
    raw = os.getenv("APP_CORS_ORIGIN_REGEX", "").strip()
    return raw or None


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=_cors_origin_regex(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)



_CLOUD_SEARCH_INDEX: QdrantIndex | None = None


def _get_search_index() -> QdrantIndex:
    """Return an initialized QdrantIndex even when APP_DISABLE_MODEL_LOAD=1.

    In Render demo mode APP_DISABLE_MODEL_LOAD=1 intentionally skips build_index()
    during import, so knowledge_assets.get_index() returns None. Search can still
    work against an already-populated Qdrant Cloud collection; it only needs a
    client + collection name, not local model preloading.
    """
    global _CLOUD_SEARCH_INDEX
    existing = get_index()
    if existing is not None:
        return existing
    if _CLOUD_SEARCH_INDEX is None:
        _CLOUD_SEARCH_INDEX = QdrantIndex(
            client=_make_qdrant_client(),
            collection_name=os.getenv("QDRANT_COLLECTION", QDRANT_COLLECTION).strip() or QDRANT_COLLECTION,
        )
    return _CLOUD_SEARCH_INDEX

def _direct_chat_answer(message: str, lang: str = "ru") -> str | None:
    text = (message or "").strip().lower().replace("ё", "е")
    compact = " ".join(text.split())
    if (
        compact in {"привет", "здравствуйте", "салам", "салем", "сәлем", "hello", "hi"}
        or compact.startswith("прив")
        or compact.startswith("салам")
        or compact.startswith("салем")
        or compact.startswith("сәлем")
    ):
        return (
            "Здравствуйте. Я AI-Talapker, демонстрационный ассистент по поступлению. "
            "Могу отвечать на вопросы по образовательным программам, документам, грантам, общежитию и срокам приёма."
        )
    if compact in {"как дела", "как ты", "қалың қалай", "калайсын", "қалайсың"}:
        return (
            "Работаю в демонстрационном режиме. Задайте вопрос по поступлению, программам, документам, грантам, "
            "общежитию или срокам приёма."
        )
    return None


def _blank_answer_fallback(lang: str = "ru") -> str:
    return (
        "Сервер получил запрос, но модель или база знаний вернула пустой ответ. "
        "Проверьте OPENROUTER_CHAT_MODEL, QDRANT_COLLECTION и наличие загруженных chunks в Qdrant."
    )


_STOPWORDS = {
    "какие", "какая", "какой", "есть", "доступны", "доступные", "для", "что", "это", "или", "при",
    "меня", "мне", "можно", "нужно", "надо", "по", "в", "на", "и", "а", "the", "what", "which",
}

_PROGRAM_MARKERS = (
    "образовательн", "программ", "специальност", "бакалавр", "магистр", "докторан",
    "информат", "математ", "software", "computer", "data", "искусствен", "машин", "втпо",
    "b0", "b057", "b058", "b059", "b063", "b064", "b065", "b066", "b067", "b068", "b071",
)

_NOISE_MARKERS_FOR_PROGRAM_QUERY = (
    "общежит", "заселени", "серпін", "серпин", "сроки", "календар", "грант", "льгот", "военн",
)


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())


def _tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-zа-яәғқңөұүһі0-9]+", (text or "").lower().replace("ё", "е")) if len(token) > 2 and token not in _STOPWORDS]


def _query_intent(question: str) -> str:
    text = (question or "").lower().replace("ё", "е")
    if any(marker in text for marker in ("общежит", "заселен", "проживан")):
        return "housing"
    if any(marker in text for marker in ("документ", "заявлен", "удостовер", "аттестат")):
        return "documents"
    if any(marker in text for marker in ("срок", "календар", "дата", "когда")):
        return "timeline"
    if any(marker in text for marker in ("грант", "серпін", "серпин", "льгот")):
        return "grants"
    if any(marker in text for marker in ("образователь", "программ", "специальност", "математ", "информат", "ент", "профиль")):
        return "programs"
    return "general"


def _hit_text_blob(hit: dict) -> str:
    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    parts = [
        hit.get("title"), hit.get("text"), hit.get("source_id"), hit.get("source_file"), hit.get("domain"),
        hit.get("class_name"), hit.get("schema"), hit.get("entry_type"), meta.get("sheet_name"), meta.get("record_type"),
        meta.get("class_name"), meta.get("domain"),
    ]
    return _clean_text(" ".join(str(part or "") for part in parts)).lower().replace("ё", "е")


def _program_hit_allowed(hit: dict, question: str) -> bool:
    blob = _hit_text_blob(hit)
    q = (question or "").lower().replace("ё", "е")
    has_program_signal = any(marker in blob for marker in _PROGRAM_MARKERS)
    if not has_program_signal:
        return False
    # Do not let broad program queries fall into dormitory/Serpin/timeline chunks.
    if not any(marker in q for marker in ("общежит", "серпін", "серпин", "срок", "календар", "грант", "льгот")):
        noisy = sum(1 for marker in _NOISE_MARKERS_FOR_PROGRAM_QUERY if marker in blob)
        if noisy and not any(marker in blob for marker in ("b0", "группа образователь", "образовательная программа", "наименование образователь")):
            return False
    return True


def _rank_hit(hit: dict, question: str, semantic_rank: int = 0) -> float:
    blob = _hit_text_blob(hit)
    q_tokens = _tokenize(question)
    score = float(hit.get("score") or 0.0) * 3.0
    if semantic_rank:
        score += max(0.0, 1.5 - semantic_rank * 0.05)
    for token in q_tokens:
        if token in blob:
            score += 2.5
        elif len(token) >= 5 and any(word.startswith(token[:5]) for word in _tokenize(blob[:3000])):
            score += 1.2
    intent = _query_intent(question)
    if intent == "programs":
        score += sum(1.8 for marker in _PROGRAM_MARKERS if marker in blob)
        if _program_hit_allowed(hit, question):
            score += 6.0
        else:
            score -= 10.0
    elif intent == "housing" and "общежит" in blob:
        score += 8.0
    elif intent == "documents" and any(marker in blob for marker in ("документ", "удостовер", "аттестат", "заявлен")):
        score += 8.0
    elif intent == "timeline" and any(marker in blob for marker in ("срок", "календар", "дата", "хронолог")):
        score += 8.0
    elif intent == "grants" and any(marker in blob for marker in ("грант", "серпін", "серпин", "льгот")):
        score += 8.0
    return score


def _scroll_payload_hits(question: str, *, limit: int = 1200) -> list[dict]:
    index = _get_search_index()
    collected: list[dict] = []
    offset = None
    remaining = max(1, int(limit))
    while remaining > 0:
        batch_size = min(256, remaining)
        points, offset = index.client.scroll(
            collection_name=index.collection_name,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points or []:
            payload = dict(getattr(point, "payload", None) or {})
            text = _clean_text(payload.get("text") or payload.get("embedding_text") or payload.get("raw_text"))
            if not text:
                continue
            hit = {
                "score": 0.0,
                "source_id": str(payload.get("source_id", "")),
                "class_name": str(payload.get("class_name", "")),
                "domain": str(payload.get("domain") or payload.get("class_name", "")),
                "schema": str(payload.get("schema", "")),
                "title": str(payload.get("title", "")),
                "text": text,
                "chunk_id": str(payload.get("chunk_id", "")),
                "logical_group_id": str(payload.get("logical_group_id") or ""),
                "entry_type": str(payload.get("entry_type") or payload.get("schema") or ""),
                "source_file": str(payload.get("source_file") or ""),
                "metadata": dict(payload.get("metadata", {}) or {}),
            }
            collected.append(hit)
        if offset is None:
            break
        remaining -= batch_size
    return collected


def _merge_and_rank_hits(question: str, semantic_hits: list[dict], lexical_hits: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for semantic_rank, hit in enumerate(semantic_hits or [], start=1):
        key = str(hit.get("chunk_id") or hit.get("logical_group_id") or (hit.get("title", "") + hit.get("text", "")[:120]))
        item = dict(hit)
        item["_semantic_rank"] = semantic_rank
        merged[key] = item
    for hit in lexical_hits or []:
        key = str(hit.get("chunk_id") or hit.get("logical_group_id") or (hit.get("title", "") + hit.get("text", "")[:120]))
        if key not in merged:
            merged[key] = dict(hit)
    intent = _query_intent(question)
    ranked = []
    for hit in merged.values():
        if not _clean_text(hit.get("text")):
            continue
        if intent == "programs" and not _program_hit_allowed(hit, question):
            continue
        rank = _rank_hit(hit, question, int(hit.get("_semantic_rank") or 0))
        hit["_demo_rank"] = round(rank, 4)
        ranked.append(hit)
    ranked.sort(key=lambda row: float(row.get("_demo_rank") or 0.0), reverse=True)
    return ranked


def _retrieve_demo_hits(question: str) -> list[dict]:
    semantic_hits: list[dict] = []
    try:
        semantic_hits = search_debug(question, _get_search_index(), top_k=30)
    except Exception:
        semantic_hits = []
    lexical_hits = _scroll_payload_hits(question, limit=int(os.getenv("APP_DEMO_SCROLL_LIMIT", "1500")))
    return _merge_and_rank_hits(question, semantic_hits, lexical_hits)[:8]


def _format_context_hit(hit: dict, index: int) -> str:
    title = str(hit.get("title") or hit.get("source_id") or hit.get("source_file") or f"Фрагмент {index}").strip()
    text = str(hit.get("text") or "").strip()
    if len(text) > 900:
        text = text[:900].rstrip() + "..."
    return f"[{index}] {title}\n{text}"


def _deterministic_rag_answer(question: str, hits: list[dict]) -> str:
    intent = _query_intent(question)
    if intent == "programs":
        header = "По базе знаний нашёл фрагменты, относящиеся к образовательным программам:"
    else:
        header = "По базе знаний нашёл релевантные фрагменты:"
    lines = [header]
    for index, hit in enumerate(hits[:4], start=1):
        title = str(hit.get("title") or hit.get("source_file") or f"Источник {index}").strip()
        text = str(hit.get("text") or "").strip()
        if not text:
            continue
        text = " ".join(text.split())
        if len(text) > 420:
            text = text[:420].rstrip() + "..."
        lines.append(f"{index}. {title}: {text}")
    if len(lines) == 1:
        return _blank_answer_fallback("ru")
    return "\n".join(lines)


def _simple_cloud_rag_answer(question: str, lang: str = "ru") -> tuple[str, list[dict]]:
    """Render-demo fallback with lexical re-ranking and intent filtering.

    The first version dumped raw semantic top-k results. On broad questions such as
    "Какие образовательные программы доступны?" FastEmbed can return adjacent but
    wrong chunks like dormitory or Serpin FAQ. This version pulls more candidates,
    ranks them by query terms + domain markers, and refuses to answer from off-intent
    chunks.
    """
    try:
        hits = _retrieve_demo_hits(question)
    except Exception as exc:
        return f"Ошибка поиска в Qdrant: {exc}", []

    hits = [hit for hit in hits if str(hit.get("text") or "").strip() and float(hit.get("_demo_rank") or 0.0) > 0.5]
    if not hits:
        return (
            "В базе знаний есть chunks, но по этому вопросу не найдено достаточно релевантных фрагментов. "
            "Проверьте, что загружены именно chunks с образовательными программами, а не только сроки, общежитие и FAQ.",
            [],
        )

    context = "\n\n".join(_format_context_hit(hit, idx) for idx, hit in enumerate(hits[:5], start=1))
    messages = [
        {
            "role": "system",
            "content": (
                "You are AI-Talapker, a university admissions assistant. "
                "Answer only using the provided context. If the context is insufficient, say so briefly. "
                "Do not list facts that are not in the context. Answer in Russian."
            ),
        },
        {
            "role": "user",
            "content": f"Question: {question}\n\nContext:\n{context}\n\nGive a concise grounded answer. Do not mention unrelated chunks.",
        },
    ]
    try:
        answer = generate_from_messages(messages, max_new_tokens=450, ctx_texts=[h.get("text", "") for h in hits[:5]]).strip()
    except Exception:
        answer = ""
    if not answer:
        answer = _deterministic_rag_answer(question, hits)
    return answer, hits


_rate_windows: dict[str, deque[float]] = defaultdict(deque)


def rate_limit(max_requests: int, window_seconds: int, key_prefix: str):
    async def dependency(request: Request) -> None:
        if os.getenv("APP_DISABLE_RATE_LIMIT", "0") == "1":
            return
        client = request.client.host if request.client else "unknown"
        key = f"{key_prefix}:{client}"
        now = time.monotonic()
        window = _rate_windows[key]
        cutoff = now - window_seconds
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= max_requests:
            raise HTTPException(status_code=429, detail="too many requests")
        window.append(now)

    return dependency


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/debug/rag")
async def debug_rag(q: str = "Какие образовательные программы есть?"):
    hits = _retrieve_demo_hits(q)
    return {
        "query": q,
        "intent": _query_intent(q),
        "hit_count": len(hits),
        "hits": hits,
    }


@app.on_event("shutdown")
async def shutdown_event():
    shutdown_knowledge_assets()
    global _CLOUD_SEARCH_INDEX
    if _CLOUD_SEARCH_INDEX is not None:
        try:
            _CLOUD_SEARCH_INDEX.client.close()
        except Exception:
            pass
        _CLOUD_SEARCH_INDEX = None

@app.post("/ask", response_model=Answer)
async def ask_question(req: Ask, _: None = Depends(rate_limit(30, 60, "ask"))):
    result = run_agent_turn(
        message=req.question,
        lang=req.lang,
        use_llm=req.use_llm,
        session_id=req.session_id,
        allow_web_search=req.allow_web_search,
    )
    return Answer(answer=result["answer"])


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _: None = Depends(rate_limit(30, 60, "chat"))):
    direct_answer = _direct_chat_answer(req.message, req.lang)
    if direct_answer:
        return ChatResponse(
            session_id=req.session_id or "",
            answer=direct_answer,
            route="direct",
            profile_complete=False,
        )

    result = run_agent_turn(
        message=req.message,
        lang=req.lang,
        use_llm=req.use_llm,
        session_id=req.session_id,
        allow_web_search=req.allow_web_search,
        reply_to=req.reply_to.model_dump() if req.reply_to else None,
        message_id=req.message_id,
    )
    answer = str(result.get("answer") or "").strip()
    route = result.get("route", "knowledge")
    if not answer:
        answer, hits = _simple_cloud_rag_answer(req.message, req.lang)
        if hits:
            route = "simple_rag_fallback"
    return ChatResponse(
        session_id=result.get("session_id") or req.session_id or "",
        answer=answer or _blank_answer_fallback(req.lang),
        route=route,
        profile_complete=bool(result.get("profile_complete")),
    )


@app.post("/suggestions", response_model=SuggestResponse)
async def suggestions(req: SuggestRequest, _: None = Depends(rate_limit(20, 60, "suggestions"))):
    safe_count = max(1, min(req.count, 8))
    if not req.use_llm:
        return SuggestResponse(questions=fallback_suggestions(req.lang, safe_count))
    if req.history:
        history = [{"role": item.role, "content": item.content} for item in req.history]
        return SuggestResponse(questions=generate_session_suggestions(get_index(), req.lang, history, safe_count))
    return SuggestResponse(questions=generate_suggestions(get_index(), req.lang, safe_count))


@app.post("/rag/rebuild", response_model=RagRebuildResponse)
async def rebuild_rag(req: RagRebuildRequest, _: dict = Depends(require_permission("rag:rebuild"))):
    stats = reload_knowledge_assets(
        rebuild_data=req.rebuild_data,
        normalize=req.normalize,
        documents=req.documents,
        chunks=req.chunks,
        index=req.index,
    )
    refresh_interview_metadata()
    return RagRebuildResponse(ok=True, **stats)


@app.post("/admin/auth/login", response_model=AdminLoginResponse)
async def admin_login(req: AdminLoginRequest, _: None = Depends(rate_limit(8, 60, "admin_login"))):
    token, expires_at = authenticate_admin(req.username, req.password)
    return AdminLoginResponse(access_token=token, expires_at=expires_at)


@app.get("/admin/auth/me")
async def admin_me(claims: dict = Depends(require_admin)):
    return {
        "username": claims.get("sub"),
        "role": claims.get("role"),
        "expires_at": claims.get("exp"),
        "auth_type": claims.get("auth_type"),
        "scopes": claims.get("scopes", []),
        "sections": claims.get("sections", []),
    }


@app.get("/admin/access/permissions")
async def admin_access_permissions(claims: dict = Depends(require_admin)):
    return permissions_payload(claims)


@app.get("/admin/access/users")
async def admin_access_users(_: dict = Depends(require_permission("admins:read"))):
    return {"users": list_admin_users(), "api_keys": list_api_keys()}


@app.post("/admin/access/users")
async def admin_access_create_user(payload: dict = Body(...), claims: dict = Depends(require_permission("admins:create"))):
    role = str(payload.get("role") or "section_admin")
    if role == "super_admin":
        raise HTTPException(status_code=403, detail="super_admin can be created only from CLI manage.py")
    username = str(payload.get("username") or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="username is required")
    user = create_admin_user(
        username=username,
        password=payload.get("password") or None,
        role=role,
        scopes=payload.get("scopes") or None,
        sections=payload.get("sections") or None,
        expires_in_minutes=payload.get("expires_in_minutes") or None,
        created_by=str(claims.get("sub") or "web"),
    )
    return {"ok": True, "user": {k: v for k, v in user.items() if k != "password"}, "temporary_password": user.get("password")}


@app.patch("/admin/access/users/{username}")
async def admin_access_update_user(username: str, payload: dict = Body(...), claims: dict = Depends(require_permission("admins:update"))):
    if payload.get("role") == "super_admin":
        raise HTTPException(status_code=403, detail="super_admin can be created only from CLI manage.py")
    user = update_admin_user(username, actor=str(claims.get("sub") or "web"), **payload)
    return {"ok": True, "user": user}


@app.post("/admin/access/users/{username}/api-keys")
async def admin_access_create_api_key(username: str, payload: dict = Body(default={}), claims: dict = Depends(require_permission("api_keys:create"))):
    key = create_api_key(
        owner_username=username,
        actor=str(claims.get("sub") or "web"),
        name=str(payload.get("name") or ""),
        scopes=payload.get("scopes") or None,
        sections=payload.get("sections") or None,
        expires_in_days=payload.get("expires_in_days") or None,
    )
    return {"ok": True, "api_key": key}


@app.post("/admin/access/api-keys/{key_id}/revoke")
async def admin_access_revoke_api_key(key_id: str, claims: dict = Depends(require_permission("api_keys:revoke"))):
    revoke_api_key(key_id, actor=str(claims.get("sub") or "web"))
    return {"ok": True, "key_id": key_id}


@app.get("/admin/access/audit-log")
async def admin_access_audit_log(limit: int = 100, _: dict = Depends(require_permission("audit:read"))):
    return {"events": list_audit_log(limit)}


def _read_jsonl(path: str) -> list[dict]:
    source = Path(path)
    if not source.exists():
        return []
    rows = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(__import__("json").loads(line))
    return rows


def _find_source_or_404(source_id: str) -> KnowledgeRegistrySource:
    source = next((item for item in load_registry() if item.source_id == source_id), None)
    if not source:
        raise HTTPException(status_code=404, detail="source not found")
    return source


@app.get("/admin/registry", response_model=RegistryResponse)
async def admin_registry(_: dict = Depends(require_permission("content:read"))):
    return RegistryResponse(sources=load_registry())


@app.post("/admin/registry", response_model=RegistryResponse)
async def update_registry(req: RegistryUpdateRequest, _: dict = Depends(require_permission("content:write"))):
    return RegistryResponse(sources=upsert_registry(req.sources))


@app.get("/admin/catalog", response_model=CatalogResponse)
async def admin_catalog(_: dict = Depends(require_permission("content:read"))):
    return CatalogResponse(catalog=load_catalog())


@app.post("/admin/catalog", response_model=CatalogResponse)
async def update_catalog(req: KnowledgeCatalog, _: dict = Depends(require_permission("content:write"))):
    return CatalogResponse(catalog=upsert_catalog(req))


@app.get("/admin/source-preview/{source_id}", response_model=SourcePreviewResponse)
async def admin_source_preview(source_id: str, _: dict = Depends(require_permission("sources:read"))):
    source = _find_source_or_404(source_id)
    source_path = safe_child_path(INPUT_DATA_DIR, source.path)
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="source file not found")
    return SourcePreviewResponse(source=source, parsed=parse_source_content(source_path))


@app.get("/admin/source-download/{source_id}")
async def admin_source_download(source_id: str, _: dict = Depends(require_permission("sources:read"))):
    source = _find_source_or_404(source_id)
    source_path = safe_child_path(INPUT_DATA_DIR, source.path)
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="source file not found")
    if not source_path.is_file():
        raise HTTPException(status_code=400, detail="source path is not a file")
    return FileResponse(
        path=source_path,
        filename=source_path.name,
        media_type="application/octet-stream",
    )


@app.post("/admin/source-mapping", response_model=RegistryResponse)
async def admin_source_mapping(req: SourceMappingUpdateRequest, _: dict = Depends(require_permission("content:write"))):
    sources = load_registry()
    updated_sources = []
    matched = False
    for source in sources:
        if source.source_id != req.source_id:
            updated_sources.append(source)
            continue
        matched = True
        domain = req.class_name or (source.items[0].domain if source.items else source.class_name)
        entry_type = req.schema_name or (source.items[0].entry_type if source.items else source.schema_name)
        entry_format = req.entry_format or _entry_format_from_legacy(entry_type or "knowledge_entry", dict(req.mapping or {}))
        items = req.items
        if items is None:
            items = [
                KnowledgeSourceItem(
                    item_id=source.source_id,
                    domain=domain,
                    entry_type=entry_type or "knowledge_entry",
                    title=req.notes if req.notes is not None else source.notes,
                    entry_format=entry_format or source._legacy_item().entry_format,
                    education_level=req.education_level if req.education_level is not None else source.education_level,
                    language=req.language if req.language is not None else source.language,
                    source_url=source.source_url,
                    notes=req.notes if req.notes is not None else source.notes,
                )
            ]
        updated_sources.append(
            source.model_copy(
                update={
                    "mapping": dict(req.mapping or {}),
                    "class_name": "",
                    "schema_name": "",
                    "items": items,
                    "education_level": req.education_level if req.education_level is not None else source.education_level,
                    "language": req.language if req.language is not None else source.language,
                    "notes": req.notes if req.notes is not None else source.notes,
                }
            )
        )
    if not matched:
        raise HTTPException(status_code=404, detail="source not found")
    return RegistryResponse(sources=upsert_registry(updated_sources))


@app.get("/admin/entries", response_model=KnowledgeEntryPreviewResponse)
async def admin_entries(
    source_id: str | None = None,
    class_name: str | None = None,
    schema_name: str | None = None,
    limit: int = 50,
    _: dict = Depends(require_permission("entries:read")),
):
    rows = _read_jsonl(KNOWLEDGE_ENTRIES_PATH)
    filtered = []
    for row in rows:
        if source_id and row.get("source_id") != source_id:
            continue
        if class_name and row.get("class_name") != class_name:
            continue
        if schema_name and row.get("schema") != schema_name:
            continue
        filtered.append(KnowledgeEntry(**row))
        if len(filtered) >= max(1, min(limit, 200)):
            break
    return KnowledgeEntryPreviewResponse(entries=filtered)


@app.get("/admin/curated-entries")
async def admin_curated_entries(_: dict = Depends(require_permission("entries:read"))):
    return {"entries": [entry.model_dump(by_alias=True) for entry in load_curated_entries()]}


@app.post("/admin/curated-entry")
async def admin_curated_entry(req: CuratedEntry, _: dict = Depends(require_permission("entries:update"))):
    entries = upsert_curated_entry(req)
    return {"ok": True, "entry": req.model_dump(by_alias=True), "entries": [entry.model_dump(by_alias=True) for entry in entries]}


@app.post("/admin/delete-entry/{entry_id}")
async def admin_delete_entry(entry_id: str, _: dict = Depends(require_permission("entries:delete"))):
    entries = delete_curated_entry(entry_id)
    return {"ok": True, "entry_id": entry_id, "entries": [entry.model_dump(by_alias=True) for entry in entries]}


@app.post("/admin/reindex-entry/{entry_id}")
async def admin_reindex_entry(entry_id: str, _: dict = Depends(require_permission("rag:reindex"))):
    entry = next((entry for entry in load_curated_entries() if entry.entry_id == entry_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="entry not found")
    stats = reindex_curated_entry(entry)
    refresh_interview_metadata()
    return {"ok": True, **stats}


@app.get("/admin/chunks")
async def admin_chunks(source_id: str | None = None, limit: int = 50, _: dict = Depends(require_permission("entries:read"))):
    rows = _read_jsonl(RAG_CHUNKS_PATH)
    filtered = []
    for row in rows:
        metadata = row.get("metadata", {}) or {}
        if source_id and metadata.get("source_id") != source_id:
            continue
        filtered.append(row)
        if len(filtered) >= max(1, min(limit, 200)):
            break
    return {"chunks": filtered}


@app.post("/admin/search-debug", response_model=SearchDebugResponse)
async def admin_search_debug(req: SearchDebugRequest, _: dict = Depends(require_permission("debug:search"))):
    hits = search_debug(
        req.query,
        get_index(),
        top_k=max(1, min(req.top_k, 20)),
        domains=req.domains or None,
        schemas=req.schemas or None,
        education_level=req.education_level,
        language=req.language,
    )
    return SearchDebugResponse(hits=[SearchDebugHit(**hit) for hit in hits])


@app.get("/admin/training/models")
async def admin_training_models(_: dict = Depends(require_permission("entries:read"))):
    return {"models": available_training_models()}


@app.get("/admin/training/datasets")
async def admin_training_datasets(_: dict = Depends(require_permission("entries:read"))):
    return {"datasets": list_datasets()}


@app.post("/admin/training/datasets")
async def admin_training_create_dataset(payload: dict = Body(...), _: dict = Depends(require_permission("entries:update"))):
    return {"ok": True, "dataset": create_dataset(payload)}


@app.get("/admin/training/datasets/{dataset_id}")
async def admin_training_get_dataset(dataset_id: str, _: dict = Depends(require_permission("entries:read"))):
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")
    return {"dataset": dataset}


@app.post("/admin/training/datasets/{dataset_id}/items")
async def admin_training_add_items(dataset_id: str, payload: dict = Body(...), _: dict = Depends(require_permission("entries:update"))):
    items = payload.get("items") if isinstance(payload, dict) else []
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items must be a list")
    result = add_dataset_items(dataset_id, items)
    if not result:
        raise HTTPException(status_code=404, detail="dataset not found")
    return {"ok": True, **result}


@app.patch("/admin/training/datasets/{dataset_id}/items/{item_id}")
async def admin_training_update_item(dataset_id: str, item_id: str, payload: dict = Body(...), _: dict = Depends(require_permission("entries:update"))):
    result = update_dataset_item(dataset_id, item_id, payload)
    if not result:
        raise HTTPException(status_code=404, detail="dataset item not found")
    return {"ok": True, **result}


@app.post("/admin/training/datasets/{dataset_id}/items/{item_id}/delete")
async def admin_training_delete_item(dataset_id: str, item_id: str, _: dict = Depends(require_permission("entries:update"))):
    result = delete_dataset_item(dataset_id, item_id)
    if not result:
        raise HTTPException(status_code=404, detail="dataset item not found")
    return {"ok": True, **result}


@app.post("/admin/training/suggest")
async def admin_training_suggest(payload: dict = Body(default={}), _: dict = Depends(require_permission("entries:update"))):
    return {"candidates": suggest_dataset_items(payload or {})}


@app.get("/admin/training/datasets/{dataset_id}/export")
async def admin_training_export_dataset(
    dataset_id: str,
    approved_only: bool = True,
    dataset_format: str | None = None,
    _: dict = Depends(require_permission("entries:read")),
):
    path = export_dataset(dataset_id, approved_only=approved_only, dataset_format=dataset_format)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="dataset not found")
    return FileResponse(path=path, filename=path.name, media_type="application/x-jsonlines")


@app.get("/admin/training/jobs")
async def admin_training_jobs(_: dict = Depends(require_permission("entries:read"))):
    return {"jobs": list_training_jobs()}


@app.post("/admin/training/jobs")
async def admin_training_create_job(payload: dict = Body(...), _: dict = Depends(require_permission("entries:update"))):
    return {"ok": True, "job": create_training_job(payload or {})}


@app.post("/admin/training/jobs/{job_id}/start")
async def admin_training_start_job(job_id: str, _: dict = Depends(require_permission("entries:update"))):
    job = start_training_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="training job not found")
    return {"ok": True, "job": job}


@app.post("/admin/training/jobs/{job_id}/stop")
async def admin_training_stop_job(job_id: str, _: dict = Depends(require_permission("entries:update"))):
    job = stop_training_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="training job not found")
    return {"ok": True, "job": job}


@app.post("/admin/upload")
async def admin_upload(
    request: Request,
    _: dict = Depends(require_permission("sources:upload")),
):
    """Upload a source file with controlled JSON errors for the admin UI."""
    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid multipart form data: {exc}") from exc

    file_value = form.get("file") or form.get("upload") or form.get("files")
    if not file_value or not hasattr(file_value, "filename") or not hasattr(file_value, "read"):
        raise HTTPException(status_code=400, detail="upload field 'file' is required")

    file = file_value
    class_name = str(form.get("class_name") or form.get("domain") or "general").strip() or "general"
    schema_name = str(form.get("schema_name") or "generic_text").strip() or "generic_text"
    education_level = str(form.get("education_level") or "").strip()
    language = str(form.get("language") or "").strip()

    max_upload_bytes = int(os.getenv("APP_MAX_UPLOAD_BYTES", str(DEFAULT_MAX_UPLOAD_BYTES)))
    filename = safe_filename(file.filename or "upload.bin")
    suffix = Path(filename).suffix.lower()
    target_dir = safe_child_path(INPUT_DATA_DIR, "ocr_raw" if suffix in OCR_INPUT_EXTENSIONS else "uploaded")
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = unique_child_path(target_dir, filename)
    bytes_written = await save_upload_file_limited(file, target_path, max_upload_bytes)

    sources = load_registry()
    normalized_schema = schema_name or "generic_text"

    if suffix in OCR_INPUT_EXTENSIONS:
        generated_root = safe_child_path(INPUT_DATA_DIR, "ocr_generated")
        try:
            result = process_ocr_upload(target_path, generated_root, original_name=file.filename or filename)
        except HTTPException:
            target_path.unlink(missing_ok=True)
            raise
        raw_rel = str(target_path.relative_to(INPUT_DATA_DIR)).replace("\\", "/")
        created_sources: list[KnowledgeRegistrySource] = []
        for generated in result.generated_files:
            generated_rel = str(generated.path.relative_to(INPUT_DATA_DIR)).replace("\\", "/")
            source_id = safe_slug(generated.path.stem, fallback="ocr_source")
            source_type = "ocr_document" if generated.kind == "document" else "ocr_tables"
            title = f"OCR {Path(file.filename or filename).stem}" if generated.kind == "document" else f"OCR таблицы {Path(file.filename or filename).stem}"
            if generated.kind == "tables":
                entry_format = EntryFormat(format="row_as_entry", header_row=1, data_start_row=2, title_column=0)
            else:
                entry_format = EntryFormat(format="section_as_entry")
            created_sources.append(
                KnowledgeRegistrySource(
                    source_id=source_id,
                    path=generated_rel,
                    source_type=source_type,
                    origin=raw_rel,
                    class_name=class_name,
                    schema=normalized_schema,
                    education_level=education_level or None,
                    language=language or None,
                    enabled=True,
                    notes=title,
                    items=[
                        KnowledgeSourceItem(
                            item_id=source_id,
                            domain=class_name,
                            entry_type=normalized_schema,
                            title=title,
                            entry_format=entry_format,
                            education_level=education_level or None,
                            language=language or None,
                            enabled=True,
                            notes=f"Generated from OCR source: {raw_rel}",
                            metadata={"origin": raw_rel, "ocr_kind": generated.kind},
                        )
                    ],
                )
            )
        generated_ids = {source.source_id for source in created_sources}
        sources = [source for source in sources if source.source_id not in generated_ids]
        sources.extend(created_sources)
        upsert_registry(sources)
        return {
            "ok": True,
            "message": "OCR завершен. В реестр добавлены созданные DOCX/XLSX источники.",
            "ocr": True,
            "raw_file": raw_rel,
            "bytes": bytes_written,
            "source_ids": [source.source_id for source in created_sources],
            "generated_files": [source.path for source in created_sources],
            "pages": len(result.pages),
            "table_rows": sum(len(page.rows) for page in result.pages),
        }

    source_id = safe_slug(target_path.stem, fallback="source")
    sources = [source for source in sources if source.source_id != source_id]
    sources.append(
        KnowledgeRegistrySource(
            source_id=source_id,
            path=str(target_path.relative_to(INPUT_DATA_DIR)).replace("\\", "/"),
            class_name=class_name,
            schema=normalized_schema,
            education_level=education_level or None,
            language=language or None,
            enabled=True,
        )
    )
    upsert_registry(sources)
    return {
        "ok": True,
        "message": "Файл загружен и добавлен в реестр источников.",
        "ocr": False,
        "source_id": source_id,
        "bytes": bytes_written,
    }


@app.post("/admin/manual-entry")
async def admin_manual_entry(
    source_id: str = Form(...),
    title: str = Form(...),
    text: str = Form(...),
    class_name: str = Form(...),
    schema_name: str = Form(...),
    education_level: str = Form(""),
    language: str = Form(""),
    _: dict = Depends(require_permission("entries:create")),
):
    target_dir = safe_child_path(INPUT_DATA_DIR, "manual")
    target_dir.mkdir(parents=True, exist_ok=True)
    source_id = safe_slug(source_id, fallback="manual")
    target_path = safe_child_path(target_dir, f"{source_id}.txt")
    target_path.write_text(text, encoding="utf-8")
    sources = load_registry()
    sources = [source for source in sources if source.source_id != source_id]
    sources.append(
        KnowledgeRegistrySource(
            source_id=source_id,
            path=str(target_path.relative_to(INPUT_DATA_DIR)).replace("\\", "/"),
            class_name=class_name,
            schema=schema_name,
            education_level=education_level or None,
            language=language or None,
            enabled=True,
            notes=title,
        )
    )
    upsert_registry(sources)
    return {"ok": True, "source_id": source_id}


@app.post("/admin/delete-source/{source_id}")
async def admin_delete_source(source_id: str, _: dict = Depends(require_permission("sources:delete"))):
    sources = load_registry()
    target = next((source for source in sources if source.source_id == source_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="source not found")
    target_path = safe_child_path(INPUT_DATA_DIR, target.path)
    if target_path.exists():
        if not target_path.is_file():
            raise HTTPException(status_code=400, detail="source path is not a file")
        target_path.unlink()
    upsert_registry([source for source in sources if source.source_id != source_id])
    return {"ok": True, "source_id": source_id}


@app.post("/admin/rebuild-source/{source_id}")
async def admin_rebuild_source(source_id: str, _: dict = Depends(require_permission("sources:reindex"))):
    stats = reindex_source(source_id, INPUT_DATA_DIR)
    refresh_interview_metadata()
    return {"ok": True, **stats}


if __name__ == "__main__":
    host = os.getenv("APP_HOST", "127.0.0.1")
    port = int(os.getenv("APP_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
