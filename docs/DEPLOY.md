# Deploy on Render Free

## 1. Create GitHub repository

Create a new repository, for example:

```text
ai-talapker-cloud-demo
```

Upload this folder content to it.

## 2. Create Qdrant Cloud collection

Use Qdrant Cloud Free cluster. Create collection for OpenRouter embeddings:

```bash
curl -X PUT \
  -H "Content-Type: application/json" \
  -H "api-key: $QDRANT_API_KEY" \
  "$QDRANT_URL/collections/ai_talapker_openrouter_1536" \
  -d '{"vectors":{"size":1536,"distance":"Cosine"}}'
```

## 3. Deploy backend

Render:

```text
New -> Web Service
Source: GitHub repo
Runtime: Docker
Root Directory: backend
Plan: Free
Health Check Path: /health
```

Environment variables:

```env
APP_ENV=demo
APP_CONFIG_FILE=configs/app.cloud.yaml
LLM_PROVIDER=openrouter
EMBEDDING_PROVIDER=openrouter
OPENROUTER_API_KEY=...
OPENROUTER_CHAT_MODEL=openrouter/free
OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
OPENROUTER_EMBEDDING_DIMENSIONS=1536
QDRANT_URL=...
QDRANT_API_KEY=...
QDRANT_COLLECTION=ai_talapker_openrouter_1536
ADMIN_JWT_SECRET=long_random_secret
APP_CORS_ORIGINS=https://n8n.kstu.kz,https://YOUR-FRONTEND.onrender.com
```

After deploy, test:

```text
https://YOUR-BACKEND.onrender.com/health
```

## 4. Load knowledge into Qdrant

In a local environment with the same env variables set:

```bash
cd backend
python scripts/cloud_qdrant_bootstrap.py --source data --collection ai_talapker_openrouter_1536 --recreate --limit 50
```

For full load, remove `--limit 50`.

## 5. Deploy frontend

Render:

```text
New -> Web Service
Source: same GitHub repo
Runtime: Docker
Root Directory: frontend
Plan: Free
```

Environment variable:

```env
BACKEND_API_BASE=https://YOUR-BACKEND.onrender.com
```

Open:

```text
https://YOUR-FRONTEND.onrender.com
https://YOUR-FRONTEND.onrender.com/admin.html
```
