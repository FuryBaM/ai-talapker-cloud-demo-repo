# Admin authentication and API permissions

This project now separates knowledge storage from access control.

- Qdrant stores only RAG vectors and payloads.
- SQLite `storage/auth.db` stores admin users, roles, API keys and audit log.
- Web UI buttons are only UX. Real access control is checked on FastAPI endpoints.

## Bootstrap / recovery

There is no default `admin/admin` login. Create a temporary CLI super admin or a permanent main admin from `assistant/`:

```bash
copy .env.example .env.local
python -c "import secrets; print(secrets.token_urlsafe(48))"
# paste that value into ADMIN_JWT_SECRET in .env.local

python manage.py create-admin --username bootstrap --role super_admin --ttl-minutes 10
python manage.py create-admin --username main_admin --role main_admin
python manage.py list-admins
```

The command prints a password and one API key. The API key is stored only as a hash and is shown once. Keep `ADMIN_JWT_SECRET` stable; changing it invalidates existing sessions and API keys.

## Roles

- `super_admin`: CLI bootstrap/recovery role. Can do everything. Prefer short TTL.
- `main_admin`: web operational administrator. Can manage content, users, API keys and audit log.
- `content_admin`: can manage knowledge sources and RAG entries.
- `section_admin`: can work with assigned sections plus entry/source operations.
- `viewer`: read-only admin.

## Endpoint protection

Use dependencies in FastAPI:

```python
Depends(require_permission("sources:upload"))
Depends(require_permission("rag:rebuild"))
Depends(require_permission("admins:create"))
```

The frontend may hide buttons based on permissions, but this is not security. Security is enforced only by backend dependencies.

## Web Components

The admin panel includes vanilla Web Components:

- `<admin-session-badge>`: shows current admin identity and role.
- `<admin-access-manager>`: manages admin users, API keys and audit log from the Advanced tab.

No npm, React or Vue is required.
