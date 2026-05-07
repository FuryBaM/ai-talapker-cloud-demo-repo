from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import string
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.config import (
    ADMIN_ALLOW_CONFIG_BOOTSTRAP,
    ADMIN_JWT_SECRET,
    ADMIN_JWT_TTL_SECONDS,
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    STORAGE_DIR,
)

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, VerificationError
except Exception:  # pragma: no cover - fallback for restricted runtimes
    PasswordHasher = None  # type: ignore
    VerifyMismatchError = VerificationError = Exception  # type: ignore


AUTH_DB_PATH = Path(os.getenv("AUTH_DB_PATH", str(Path(STORAGE_DIR) / "auth.db")))
_bearer = HTTPBearer(auto_error=False)

ROLE_DEFAULT_SCOPES: dict[str, list[str]] = {
    "super_admin": ["*"],
    "main_admin": [
        "admins:read",
        "admins:create",
        "admins:update",
        "admins:disable",
        "api_keys:read",
        "api_keys:create",
        "api_keys:revoke",
        "audit:read",
        "content:read",
        "content:write",
        "sources:read",
        "sources:upload",
        "sources:delete",
        "sources:reindex",
        "entries:read",
        "entries:create",
        "entries:update",
        "entries:delete",
        "rag:rebuild",
        "rag:reindex",
        "debug:search",
    ],
    "content_admin": [
        "content:read",
        "content:write",
        "sources:read",
        "sources:upload",
        "sources:reindex",
        "entries:read",
        "entries:create",
        "entries:update",
        "entries:delete",
        "rag:reindex",
        "debug:search",
    ],
    "section_admin": [
        "content:read",
        "sources:read",
        "entries:read",
        "entries:create",
        "entries:update",
        "sources:reindex",
    ],
    "viewer": ["content:read", "sources:read", "entries:read"],
    # Backward-compatible role for old single-admin tokens/configs.
    "admin": [
        "admins:read",
        "content:read",
        "content:write",
        "sources:read",
        "sources:upload",
        "sources:delete",
        "sources:reindex",
        "entries:read",
        "entries:create",
        "entries:update",
        "entries:delete",
        "rag:rebuild",
        "rag:reindex",
        "debug:search",
        "audit:read",
    ],
}

ROLE_LABELS: dict[str, str] = {
    "super_admin": "Суперадмин CLI",
    "main_admin": "Главный админ",
    "content_admin": "Контент-админ",
    "section_admin": "Обычный админ раздела",
    "viewer": "Наблюдатель",
    "admin": "Legacy admin",
}


def _now() -> int:
    return int(time.time())


def _connect() -> sqlite3.Connection:
    AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _json_loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def init_auth_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS admin_users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'section_admin',
                scopes_json TEXT NOT NULL DEFAULT '[]',
                sections_json TEXT NOT NULL DEFAULT '[]',
                disabled INTEGER NOT NULL DEFAULT 0,
                expires_at INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                last_login_at INTEGER,
                created_by TEXT
            );

            CREATE TABLE IF NOT EXISTS api_keys (
                key_id TEXT PRIMARY KEY,
                owner_username TEXT NOT NULL,
                key_hash TEXT NOT NULL UNIQUE,
                key_prefix TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                scopes_json TEXT NOT NULL DEFAULT '[]',
                sections_json TEXT NOT NULL DEFAULT '[]',
                revoked INTEGER NOT NULL DEFAULT 0,
                expires_at INTEGER,
                created_at INTEGER NOT NULL,
                last_used_at INTEGER,
                created_by TEXT,
                FOREIGN KEY(owner_username) REFERENCES admin_users(username) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target TEXT NOT NULL DEFAULT '',
                detail_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL
            );
            """
        )


def _hash_password(password: str) -> str:
    if PasswordHasher:
        return PasswordHasher().hash(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return "pbkdf2_sha256$" + base64.b64encode(salt).decode("ascii") + "$" + base64.b64encode(digest).decode("ascii")


def _verify_password(password_hash: str, password: str) -> bool:
    if password_hash.startswith("pbkdf2_sha256$"):
        try:
            _, salt_b64, digest_b64 = password_hash.split("$", 2)
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(digest_b64)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False
    if not PasswordHasher:
        return False
    try:
        return PasswordHasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def _password_needs_rehash(password_hash: str) -> bool:
    return bool(PasswordHasher and not password_hash.startswith("pbkdf2_sha256$") and PasswordHasher().check_needs_rehash(password_hash))


def generate_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def normalize_scopes(role: str, scopes: list[str] | None = None) -> list[str]:
    result = list(ROLE_DEFAULT_SCOPES.get(role, []))
    for scope in scopes or []:
        scope = str(scope).strip()
        if scope and scope not in result:
            result.append(scope)
    return result


def log_audit(actor: str, action: str, target: str = "", detail: dict[str, Any] | None = None) -> None:
    init_auth_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_log(actor, action, target, detail_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (actor or "system", action, target or "", _json_dumps(detail or {}), _now()),
        )


def _row_to_user(row: sqlite3.Row) -> dict[str, Any]:
    role = row["role"]
    return {
        "username": row["username"],
        "role": role,
        "role_label": ROLE_LABELS.get(role, role),
        "scopes": _json_loads(row["scopes_json"], normalize_scopes(role)),
        "sections": _json_loads(row["sections_json"], []),
        "disabled": bool(row["disabled"]),
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_login_at": row["last_login_at"],
        "created_by": row["created_by"],
    }


def _get_user_row(username: str) -> sqlite3.Row | None:
    init_auth_db()
    with _connect() as conn:
        return conn.execute("SELECT * FROM admin_users WHERE username = ?", (username,)).fetchone()


def _auth_user_or_error(username: str) -> dict[str, Any]:
    row = _get_user_row(username)
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Admin user not found")
    user = _row_to_user(row)
    if user["disabled"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin user is disabled")
    if user["expires_at"] and int(user["expires_at"]) <= _now():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin user expired")
    return user


def count_admin_users() -> int:
    init_auth_db()
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0])


def create_admin_user(
    username: str,
    password: str | None = None,
    role: str = "main_admin",
    scopes: list[str] | None = None,
    sections: list[str] | None = None,
    expires_in_minutes: int | None = None,
    created_by: str = "system",
) -> dict[str, Any]:
    init_auth_db()
    username = str(username or "").strip()
    if not username:
        raise ValueError("username is required")
    if role not in ROLE_DEFAULT_SCOPES:
        raise ValueError(f"unknown role: {role}")
    generated_password = password or generate_password()
    expires_at = _now() + int(expires_in_minutes) * 60 if expires_in_minutes else None
    now = _now()
    final_scopes = normalize_scopes(role, scopes)
    final_sections = [str(item).strip() for item in (sections or []) if str(item).strip()]
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO admin_users(username, password_hash, role, scopes_json, sections_json, disabled, expires_at, created_at, updated_at, created_by)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                password_hash=excluded.password_hash,
                role=excluded.role,
                scopes_json=excluded.scopes_json,
                sections_json=excluded.sections_json,
                disabled=0,
                expires_at=excluded.expires_at,
                updated_at=excluded.updated_at,
                created_by=excluded.created_by
            """,
            (username, _hash_password(generated_password), role, _json_dumps(final_scopes), _json_dumps(final_sections), expires_at, now, now, created_by),
        )
    log_audit(created_by, "admin_user.upsert", username, {"role": role, "expires_at": expires_at, "sections": final_sections})
    return {"username": username, "role": role, "password": generated_password, "expires_at": expires_at, "scopes": final_scopes, "sections": final_sections}


def update_admin_user(username: str, actor: str, **changes: Any) -> dict[str, Any]:
    current = _auth_user_or_error(username)
    role = str(changes.get("role") or current["role"])
    if role not in ROLE_DEFAULT_SCOPES:
        raise ValueError(f"unknown role: {role}")
    scopes = changes.get("scopes")
    if scopes is None:
        scopes = current["scopes"]
    sections = changes.get("sections")
    if sections is None:
        sections = current["sections"]
    disabled = bool(changes.get("disabled", current["disabled"]))
    expires_at = changes.get("expires_at", current["expires_at"])
    now = _now()
    with _connect() as conn:
        params: list[Any] = [role, _json_dumps(scopes), _json_dumps(sections), int(disabled), expires_at, now]
        sql = "UPDATE admin_users SET role=?, scopes_json=?, sections_json=?, disabled=?, expires_at=?, updated_at=?"
        if changes.get("password"):
            sql += ", password_hash=?"
            params.append(_hash_password(str(changes["password"])))
        sql += " WHERE username=?"
        params.append(username)
        conn.execute(sql, tuple(params))
    log_audit(actor, "admin_user.update", username, {k: v for k, v in changes.items() if k != "password"})
    return _auth_user_or_error(username)


def list_admin_users() -> list[dict[str, Any]]:
    init_auth_db()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM admin_users ORDER BY created_at DESC, username ASC").fetchall()
    return [_row_to_user(row) for row in rows]


def _b64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")


def _b64url_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode((payload + padding).encode("utf-8"))


def _sign(message: str) -> str:
    signature = hmac.new(ADMIN_JWT_SECRET.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_encode(signature)


def create_admin_token(username: str) -> tuple[str, int]:
    user = _auth_user_or_error(username)
    expires_at = _now() + ADMIN_JWT_TTL_SECONDS
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": username,
        "role": user["role"],
        "scopes": user["scopes"],
        "sections": user["sections"],
        "exp": expires_at,
        "typ": "admin_access",
    }
    encoded_header = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    message = f"{encoded_header}.{encoded_payload}"
    return f"{message}.{_sign(message)}", expires_at


def _verify_jwt(token: str) -> dict[str, Any]:
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token format") from exc
    message = f"{encoded_header}.{encoded_payload}"
    expected = _sign(message)
    if not hmac.compare_digest(encoded_signature, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token signature")
    payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
    if int(payload.get("exp", 0)) <= _now():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    username = str(payload.get("sub") or "")
    if not username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token subject")
    # Re-read user from SQLite so disabling an account invalidates existing tokens.
    user = _auth_user_or_error(username)
    payload["role"] = user["role"]
    payload["scopes"] = user["scopes"]
    payload["sections"] = user["sections"]
    payload["auth_type"] = "jwt"
    return payload


def _hash_api_key(api_key: str) -> str:
    return hmac.new(ADMIN_JWT_SECRET.encode("utf-8"), api_key.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_api_key() -> str:
    return "atk_live_" + secrets.token_urlsafe(32)


def create_api_key(
    owner_username: str,
    actor: str = "system",
    name: str = "",
    scopes: list[str] | None = None,
    sections: list[str] | None = None,
    expires_in_days: int | None = None,
) -> dict[str, Any]:
    owner = _auth_user_or_error(owner_username)
    api_key = generate_api_key()
    key_id = "key_" + secrets.token_hex(8)
    key_prefix = api_key[:18]
    expires_at = _now() + int(expires_in_days) * 86400 if expires_in_days else None
    final_scopes = scopes if scopes is not None else owner["scopes"]
    final_sections = sections if sections is not None else owner["sections"]
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO api_keys(key_id, owner_username, key_hash, key_prefix, name, scopes_json, sections_json, revoked, expires_at, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (key_id, owner_username, _hash_api_key(api_key), key_prefix, name or "", _json_dumps(final_scopes), _json_dumps(final_sections), expires_at, _now(), actor),
        )
    log_audit(actor, "api_key.create", owner_username, {"key_id": key_id, "name": name, "expires_at": expires_at})
    return {"key_id": key_id, "owner_username": owner_username, "api_key": api_key, "key_prefix": key_prefix, "scopes": final_scopes, "sections": final_sections, "expires_at": expires_at}


def _verify_api_key(api_key: str) -> dict[str, Any]:
    init_auth_db()
    key_hash = _hash_api_key(api_key)
    with _connect() as conn:
        row = conn.execute("SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)).fetchone()
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        if row["revoked"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key revoked")
        if row["expires_at"] and int(row["expires_at"]) <= _now():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key expired")
        user = _auth_user_or_error(row["owner_username"])
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE key_id = ?", (_now(), row["key_id"]))
    return {
        "sub": row["owner_username"],
        "role": user["role"],
        "scopes": _json_loads(row["scopes_json"], user["scopes"]),
        "sections": _json_loads(row["sections_json"], user["sections"]),
        "auth_type": "api_key",
        "key_id": row["key_id"],
        "exp": row["expires_at"],
    }


def list_api_keys(owner_username: str | None = None) -> list[dict[str, Any]]:
    init_auth_db()
    with _connect() as conn:
        if owner_username:
            rows = conn.execute("SELECT * FROM api_keys WHERE owner_username = ? ORDER BY created_at DESC", (owner_username,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
    return [
        {
            "key_id": row["key_id"],
            "owner_username": row["owner_username"],
            "key_prefix": row["key_prefix"],
            "name": row["name"],
            "scopes": _json_loads(row["scopes_json"], []),
            "sections": _json_loads(row["sections_json"], []),
            "revoked": bool(row["revoked"]),
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "last_used_at": row["last_used_at"],
            "created_by": row["created_by"],
        }
        for row in rows
    ]


def revoke_api_key(key_id: str, actor: str) -> None:
    init_auth_db()
    with _connect() as conn:
        conn.execute("UPDATE api_keys SET revoked = 1 WHERE key_id = ?", (key_id,))
    log_audit(actor, "api_key.revoke", key_id)


def list_audit_log(limit: int = 100) -> list[dict[str, Any]]:
    init_auth_db()
    safe_limit = max(1, min(int(limit or 100), 500))
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (safe_limit,)).fetchall()
    return [
        {
            "id": row["id"],
            "actor": row["actor"],
            "action": row["action"],
            "target": row["target"],
            "detail": _json_loads(row["detail_json"], {}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def authenticate_admin(username: str, password: str) -> tuple[str, int]:
    init_auth_db()
    row = _get_user_row(username)
    if row:
        if not _verify_password(row["password_hash"], password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
        user = _auth_user_or_error(username)
        with _connect() as conn:
            conn.execute("UPDATE admin_users SET last_login_at = ? WHERE username = ?", (_now(), username))
            if _password_needs_rehash(row["password_hash"]):
                conn.execute("UPDATE admin_users SET password_hash = ?, updated_at = ? WHERE username = ?", (_hash_password(password), _now(), username))
        log_audit(username, "auth.login", username, {"role": user["role"]})
        return create_admin_token(username)

    # Optional migration bootstrap. Disabled by default; never rely on admin/admin.
    if (
        ADMIN_ALLOW_CONFIG_BOOTSTRAP
        and count_admin_users() == 0
        and ADMIN_USERNAME
        and ADMIN_PASSWORD
        and username == ADMIN_USERNAME
        and password == ADMIN_PASSWORD
        and not (ADMIN_USERNAME == "admin" and ADMIN_PASSWORD == "admin")
    ):
        create_admin_user(username=ADMIN_USERNAME, password=ADMIN_PASSWORD, role="main_admin", created_by="config_bootstrap")
        return create_admin_token(ADMIN_USERNAME)

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")


def verify_admin_token(token: str) -> dict[str, Any]:
    if token.startswith("atk_live_"):
        return _verify_api_key(token)
    return _verify_jwt(token)


def require_admin(credentials: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict[str, Any]:
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    return verify_admin_token(credentials.credentials)


def has_permission(claims: dict[str, Any], permission: str, section: str | None = None) -> bool:
    scopes = set(claims.get("scopes") or [])
    role = str(claims.get("role") or "")
    if role == "super_admin" or "*" in scopes:
        return True
    if permission in scopes:
        return True
    if f"{permission}:any" in scopes:
        return True
    if section:
        section = str(section).strip()
        if f"{permission}:{section}" in scopes:
            return True
        sections = set(str(item) for item in (claims.get("sections") or []))
        if section in sections and permission in scopes:
            return True
    return False


def require_permission(permission: str, section_param: str | None = None) -> Callable[..., dict[str, Any]]:
    def dependency(claims: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
        # section_param is intentionally kept for route-specific wrappers; simple routes use permission only.
        if not has_permission(claims, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing permission: {permission}")
        return claims

    return dependency


def permissions_payload(current: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "roles": [
            {"role": role, "label": ROLE_LABELS.get(role, role), "default_scopes": scopes}
            for role, scopes in ROLE_DEFAULT_SCOPES.items()
            if role != "admin"
        ],
        "current": current or {},
    }
