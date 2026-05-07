from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from core.config import STORAGE_DIR
from core.knowledge_registry import load_registry, upsert_registry
from core.schemas import KnowledgeSourceItem, _entry_format_from_legacy

VERSIONS_PATH = Path(STORAGE_DIR) / "admin_source_versions.json"
CHANGE_REQUESTS_PATH = Path(STORAGE_DIR) / "admin_change_requests.json"


def _now() -> int:
    return int(time.time())


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "null")
        return payload if payload is not None else default
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _author(claims: dict[str, Any] | None) -> str:
    claims = claims or {}
    return str(claims.get("sub") or claims.get("username") or "admin").strip() or "admin"


def _source_dict(source: Any) -> dict[str, Any]:
    if hasattr(source, "model_dump"):
        return source.model_dump(by_alias=True)
    return dict(source or {})


def _canonical_hash(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def find_source(source_id: str) -> Any | None:
    return next((item for item in load_registry() if item.source_id == source_id), None)


def list_source_versions(source_id: str) -> list[dict[str, Any]]:
    versions = _read_json(VERSIONS_PATH, [])
    if not isinstance(versions, list):
        return []
    rows = [item for item in versions if isinstance(item, dict) and item.get("source_id") == source_id]
    return sorted(rows, key=lambda item: int(item.get("created_at") or 0), reverse=True)


def create_source_snapshot(source_id: str, claims: dict[str, Any] | None, title: str = "", note: str = "") -> dict[str, Any] | None:
    source = find_source(source_id)
    if not source:
        return None
    source_payload = _source_dict(source)
    versions = _read_json(VERSIONS_PATH, [])
    if not isinstance(versions, list):
        versions = []
    version = {
        "version_id": uuid.uuid4().hex[:12],
        "resource_type": "source",
        "source_id": source_id,
        "title": str(title or "").strip() or f"Snapshot {source_id}",
        "note": str(note or "").strip(),
        "author": _author(claims),
        "created_at": _now(),
        "source_hash": _canonical_hash(source_payload),
        "source": source_payload,
    }
    versions.insert(0, version)
    _write_json(VERSIONS_PATH, versions[:500])
    return version


def list_change_requests(source_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    requests = _read_json(CHANGE_REQUESTS_PATH, [])
    if not isinstance(requests, list):
        return []
    rows = [item for item in requests if isinstance(item, dict)]
    if source_id:
        rows = [item for item in rows if item.get("source_id") == source_id]
    if status:
        rows = [item for item in rows if item.get("status") == status]
    return sorted(rows, key=lambda item: int(item.get("created_at") or 0), reverse=True)


def create_change_request(payload: dict[str, Any], claims: dict[str, Any] | None) -> dict[str, Any] | None:
    source_id = str(payload.get("source_id") or payload.get("resource_id") or "").strip()
    source = find_source(source_id)
    if not source:
        return None
    current_source = _source_dict(source)
    proposed = payload.get("proposed_source") if isinstance(payload.get("proposed_source"), dict) else {}
    if not proposed:
        proposed = dict(current_source)
    proposed.setdefault("source_id", source_id)
    requests = _read_json(CHANGE_REQUESTS_PATH, [])
    if not isinstance(requests, list):
        requests = []
    request = {
        "request_id": uuid.uuid4().hex[:12],
        "resource_type": "source",
        "source_id": source_id,
        "title": str(payload.get("title") or "").strip() or f"Update {source_id}",
        "description": str(payload.get("description") or "").strip(),
        "author": _author(claims),
        "status": "pending",
        "created_at": _now(),
        "updated_at": _now(),
        "base_hash": _canonical_hash(current_source),
        "current_hash_at_submit": _canonical_hash(current_source),
        "proposed_source": proposed,
        "review": {},
    }
    requests.insert(0, request)
    _write_json(CHANGE_REQUESTS_PATH, requests[:500])
    return request


def _update_request(request_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    requests = _read_json(CHANGE_REQUESTS_PATH, [])
    if not isinstance(requests, list):
        return None
    for index, item in enumerate(requests):
        if isinstance(item, dict) and item.get("request_id") == request_id:
            updated = dict(item)
            updated.update(patch)
            updated["updated_at"] = _now()
            requests[index] = updated
            _write_json(CHANGE_REQUESTS_PATH, requests)
            return updated
    return None


def reject_change_request(request_id: str, claims: dict[str, Any] | None, reason: str = "") -> dict[str, Any] | None:
    return _update_request(request_id, {
        "status": "rejected",
        "review": {"reviewer": _author(claims), "reviewed_at": _now(), "reason": str(reason or "").strip()},
    })


def _apply_source_payload(proposed: dict[str, Any]) -> list[Any]:
    source_id = str(proposed.get("source_id") or "").strip()
    sources = load_registry()
    updated_sources = []
    matched = False
    for source in sources:
        if source.source_id != source_id:
            updated_sources.append(source)
            continue
        matched = True
        mapping = dict(proposed.get("mapping") if isinstance(proposed.get("mapping"), dict) else source.mapping or {})
        domain = str(proposed.get("class_name") or proposed.get("domain") or (source.items[0].domain if source.items else source.class_name) or "").strip()
        entry_type = str(proposed.get("schema_name") or proposed.get("schema") or (source.items[0].entry_type if source.items else source.schema_name) or "knowledge_entry").strip()
        raw_items = proposed.get("items")
        if isinstance(raw_items, list) and raw_items:
            items = [KnowledgeSourceItem(**item) if isinstance(item, dict) else item for item in raw_items]
        else:
            entry_format = _entry_format_from_legacy(entry_type or "knowledge_entry", mapping)
            items = [KnowledgeSourceItem(
                item_id=source.source_id,
                domain=domain,
                entry_type=entry_type or "knowledge_entry",
                title=proposed.get("notes") if proposed.get("notes") is not None else source.notes,
                entry_format=entry_format,
                education_level=proposed.get("education_level") if proposed.get("education_level") is not None else source.education_level,
                language=proposed.get("language") if proposed.get("language") is not None else source.language,
                source_url=source.source_url,
                notes=proposed.get("notes") if proposed.get("notes") is not None else source.notes,
            )]
        updated_sources.append(source.model_copy(update={
            "mapping": mapping,
            "class_name": "",
            "schema_name": "",
            "items": items,
            "education_level": proposed.get("education_level") if proposed.get("education_level") is not None else source.education_level,
            "language": proposed.get("language") if proposed.get("language") is not None else source.language,
            "notes": proposed.get("notes") if proposed.get("notes") is not None else source.notes,
        }))
    if not matched:
        raise ValueError("source not found")
    return upsert_registry(updated_sources)


def approve_change_request(request_id: str, claims: dict[str, Any] | None, *, force: bool = False) -> dict[str, Any] | None:
    requests = list_change_requests()
    request = next((item for item in requests if item.get("request_id") == request_id), None)
    if not request:
        return None
    if request.get("status") != "pending":
        return request
    source = find_source(str(request.get("source_id") or ""))
    if not source:
        return None
    current_payload = _source_dict(source)
    current_hash = _canonical_hash(current_payload)
    if current_hash != request.get("base_hash") and not force:
        return _update_request(request_id, {
            "status": "conflict",
            "review": {
                "reviewer": _author(claims),
                "reviewed_at": _now(),
                "reason": "current source changed after request was created",
                "current_hash": current_hash,
                "base_hash": request.get("base_hash"),
            },
        })
    before = create_source_snapshot(str(request.get("source_id") or ""), claims, title=f"Before request {request_id}")
    sources = _apply_source_payload(dict(request.get("proposed_source") or {}))
    approved = _update_request(request_id, {
        "status": "approved",
        "review": {
            "reviewer": _author(claims),
            "reviewed_at": _now(),
            "before_version_id": (before or {}).get("version_id"),
            "forced": bool(force),
        },
    })
    return {"request": approved, "sources": [item.model_dump(by_alias=True) for item in sources]}


def restore_source_version(version_id: str, claims: dict[str, Any] | None) -> dict[str, Any] | None:
    versions = _read_json(VERSIONS_PATH, [])
    if not isinstance(versions, list):
        return None
    version = next((item for item in versions if isinstance(item, dict) and item.get("version_id") == version_id), None)
    if not version:
        return None
    source_id = str(version.get("source_id") or "")
    create_source_snapshot(source_id, claims, title=f"Before restore {version_id}")
    sources = _apply_source_payload(dict(version.get("source") or {}))
    return {"version": version, "sources": [item.model_dump(by_alias=True) for item in sources]}
