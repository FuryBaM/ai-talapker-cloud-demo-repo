import os
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
from core.config import INPUT_DATA_DIR, KNOWLEDGE_ENTRIES_PATH, KNOWLEDGE_REGISTRY_PATH, RAG_CHUNKS_PATH
from core.entry_store import delete_curated_entry, load_curated_entries, upsert_curated_entry
from core.generation import fallback_suggestions, generate_session_suggestions, generate_suggestions
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
from core.rag import search_debug
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


@app.on_event("shutdown")
async def shutdown_event():
    shutdown_knowledge_assets()

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
    result = run_agent_turn(
        message=req.message,
        lang=req.lang,
        use_llm=req.use_llm,
        session_id=req.session_id,
        allow_web_search=req.allow_web_search,
        reply_to=req.reply_to.model_dump() if req.reply_to else None,
        message_id=req.message_id,
    )
    return ChatResponse(
        session_id=result["session_id"],
        answer=result["answer"],
        route=result.get("route", "knowledge"),
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
