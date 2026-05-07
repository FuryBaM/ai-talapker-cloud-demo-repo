# Backend service

Docker-ready FastAPI service for Render/Koyeb.

Render settings:

```text
Runtime: Docker
Root Directory: backend
Health Check Path: /health
```

Required env variables are in `.env.render.example`.

The container starts with:

```bash
uvicorn app:app --host 0.0.0.0 --port ${PORT:-10000}
```
