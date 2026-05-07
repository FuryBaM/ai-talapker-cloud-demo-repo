# Security notes

Applied hardening:

- `start.bat` binds API to `127.0.0.1`; LAN exposure is explicit through `start-lan.bat`.
- Default `admin/admin` bootstrap is disabled.
- Unsafe or missing `ADMIN_JWT_SECRET` is replaced with an ephemeral runtime secret. Set a real secret in `.env.local`.
- CORS defaults to localhost. LAN mode uses a private-LAN origin regex instead of unrestricted wildcard CORS.
- `/admin/auth/login`, `/ask`, `/chat`, and `/suggestions` have in-memory IP rate limits.
- Registry source file access is contained inside `input_data`.
- Uploads are limited to `.txt`, `.docx`, `.xlsx`, have a size limit, and are saved under unique sanitized filenames.
- Manual entry `source_id` is sanitized before it becomes a filename.
- Redis Docker port is bound to `127.0.0.1`.

Local setup:

```bash
cd assistant
copy .env.example .env.local
python -c "import secrets; print(secrets.token_urlsafe(48))"
python manage.py create-admin --username main_admin --role main_admin
start.bat
```

For LAN testing, set `ADMIN_JWT_SECRET` in `.env.local`, run `assistant/start-lan.bat`, then run `web-assistant-panel/start-lan.bat`.

## Cross-platform startup scripts

Use `RUN_GUIDE.md` in the project root for Windows/Linux startup.

Linux scripts added:

```bash
bash assistant/setup-linux.sh
bash assistant/start.sh
bash web-assistant-panel/start.sh
```

Windows scripts added:

```bat
assistant\setup-windows.bat
RUN_WINDOWS.bat
```
