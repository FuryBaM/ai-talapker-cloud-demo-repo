from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from core.config import STORAGE_DIR

COLLAB_PATH = Path(STORAGE_DIR) / "admin_collaboration.json"
PRESENCE_TTL_SECONDS = 45
LOCK_TTL_SECONDS = 120


def _now() -> int:
    return int(time.time())


def _read_state() -> dict[str, Any]:
    if not COLLAB_PATH.exists():
        return {"presence": {}, "locks": {}}
    try:
        payload = json.loads(COLLAB_PATH.read_text(encoding="utf-8") or "{}")
        if not isinstance(payload, dict):
            return {"presence": {}, "locks": {}}
        payload.setdefault("presence", {})
        payload.setdefault("locks", {})
        return payload
    except Exception:
        return {"presence": {}, "locks": {}}


def _write_state(payload: dict[str, Any]) -> None:
    COLLAB_PATH.parent.mkdir(parents=True, exist_ok=True)
    COLLAB_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resource_key(resource_type: str, resource_id: str) -> str:
    return f"{str(resource_type or 'resource').strip()}:{str(resource_id or '').strip()}"


def _actor(claims: dict[str, Any] | None, client_id: str = "") -> dict[str, str]:
    claims = claims or {}
    username = str(claims.get("sub") or claims.get("username") or "admin").strip() or "admin"
    role = str(claims.get("role") or "").strip()
    return {"username": username, "role": role, "client_id": str(client_id or "").strip()}


def _same_actor(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if not a or not b:
        return False
    if str(a.get("client_id") or "") and str(b.get("client_id") or ""):
        return str(a.get("client_id")) == str(b.get("client_id"))
    return str(a.get("username") or "") == str(b.get("username") or "")


def _cleanup(payload: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    presence = payload.get("presence") if isinstance(payload.get("presence"), dict) else {}
    payload["presence"] = {
        key: value for key, value in presence.items()
        if isinstance(value, dict) and int(value.get("expires_at") or 0) > now
    }
    locks = payload.get("locks") if isinstance(payload.get("locks"), dict) else {}
    payload["locks"] = {
        key: value for key, value in locks.items()
        if isinstance(value, dict) and int(value.get("expires_at") or 0) > now
    }
    return payload


def _resource_snapshot(payload: dict[str, Any], resource_type: str, resource_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    key = _resource_key(resource_type, resource_id)
    editors = []
    for item in payload.get("presence", {}).values():
        if item.get("resource_key") == key:
            editors.append({k: item.get(k) for k in ("username", "role", "client_id", "started_at", "updated_at", "expires_at")})
    editors.sort(key=lambda item: str(item.get("username") or ""))
    lock = payload.get("locks", {}).get(key)
    locked_by_other = bool(lock and not _same_actor(lock, actor))
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "editors": editors,
        "others": [item for item in editors if not _same_actor(item, actor)],
        "lock": lock,
        "locked_by_other": locked_by_other,
        "now": _now(),
    }


def heartbeat_resource(resource_type: str, resource_id: str, claims: dict[str, Any] | None, client_id: str = "") -> dict[str, Any]:
    actor = _actor(claims, client_id)
    payload = _cleanup(_read_state())
    now = _now()
    key = _resource_key(resource_type, resource_id)
    session_key = f"{key}:{actor['username']}:{actor['client_id'] or 'default'}"
    previous = payload["presence"].get(session_key, {})
    payload["presence"][session_key] = {
        **actor,
        "resource_key": key,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "started_at": int(previous.get("started_at") or now),
        "updated_at": now,
        "expires_at": now + PRESENCE_TTL_SECONDS,
    }
    lock = payload.get("locks", {}).get(key)
    if lock and _same_actor(lock, actor):
        lock["updated_at"] = now
        lock["expires_at"] = now + LOCK_TTL_SECONDS
        payload["locks"][key] = lock
    _write_state(payload)
    return _resource_snapshot(payload, resource_type, resource_id, actor)


def get_resource_presence(resource_type: str, resource_id: str, claims: dict[str, Any] | None, client_id: str = "") -> dict[str, Any]:
    actor = _actor(claims, client_id)
    payload = _cleanup(_read_state())
    _write_state(payload)
    return _resource_snapshot(payload, resource_type, resource_id, actor)


def acquire_resource_lock(resource_type: str, resource_id: str, claims: dict[str, Any] | None, client_id: str = "") -> dict[str, Any]:
    actor = _actor(claims, client_id)
    payload = _cleanup(_read_state())
    now = _now()
    key = _resource_key(resource_type, resource_id)
    current = payload.get("locks", {}).get(key)
    if current and not _same_actor(current, actor):
        return {**_resource_snapshot(payload, resource_type, resource_id, actor), "ok": False, "reason": "locked_by_other"}
    lock = {
        **actor,
        "resource_key": key,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "started_at": int((current or {}).get("started_at") or now),
        "updated_at": now,
        "expires_at": now + LOCK_TTL_SECONDS,
    }
    payload.setdefault("locks", {})[key] = lock
    _write_state(payload)
    return {**_resource_snapshot(payload, resource_type, resource_id, actor), "ok": True}


def release_resource_lock(resource_type: str, resource_id: str, claims: dict[str, Any] | None, client_id: str = "") -> dict[str, Any]:
    actor = _actor(claims, client_id)
    payload = _cleanup(_read_state())
    key = _resource_key(resource_type, resource_id)
    current = payload.get("locks", {}).get(key)
    if current and _same_actor(current, actor):
        payload["locks"].pop(key, None)
    _write_state(payload)
    return {**_resource_snapshot(payload, resource_type, resource_id, actor), "ok": True}


def is_locked_by_other(resource_type: str, resource_id: str, claims: dict[str, Any] | None, client_id: str = "") -> dict[str, Any] | None:
    actor = _actor(claims, client_id)
    payload = _cleanup(_read_state())
    key = _resource_key(resource_type, resource_id)
    current = payload.get("locks", {}).get(key)
    if current and not _same_actor(current, actor):
        return current
    return None
