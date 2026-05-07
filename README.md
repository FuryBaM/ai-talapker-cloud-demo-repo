# AI-Talapker Cloud Demo

Deployment repository for the Telegram/n8n cloud demonstration.

Architecture:

```text
Telegram -> n8n.kstu.kz -> Render backend -> OpenRouter + Qdrant Cloud -> n8n -> Telegram
Browser  -> Render frontend -> Render backend
```

Folders:

```text
backend/   FastAPI + LangGraph/RAG backend, Docker-ready for Render/Koyeb
frontend/  Static chat/admin UI served by nginx, Docker-ready for Render
n8n/       Importable n8n workflow for Telegram -> backend proxy
docs/      Deployment notes
```

The deployment keeps these backend paths unchanged:

```text
backend/models
backend/data
backend/input_data
```

Do not commit real API keys. Put them only into Render environment variables.
