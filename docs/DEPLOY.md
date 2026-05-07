# Deploy full cloud version on Render Free

This version keeps the normal FastAPI + LangGraph backend. n8n is only the Telegram integration layer. The backend itself performs routing, retrieval, recommendation logic, Qdrant search, OpenRouter generation, admin APIs, and frontend APIs.

## 1. Required services

```text
Render backend      = FastAPI + LangGraph
Render frontend     = static UI/admin UI
n8n.kstu.kz         = Telegram webhook/orchestration
Qdrant Cloud Free   = vector database
OpenRouter          = chat model only
FastEmbed           = free CPU embeddings inside backend/local bootstrap
```

OpenRouter embeddings are not used in this version.

## 2. Qdrant Cloud collection

Create this collection:

```text
name: ai_talapker_fastembed_384
vector size: 384
distance: Cosine
```

PowerShell/curl equivalent:

```bash
curl -X PUT   -H "Content-Type: application/json"   -H "api-key: $QDRANT_API_KEY"   "$QDRANT_URL/collections/ai_talapker_fastembed_384"   -d '{"vectors":{"size":384,"distance":"Cosine"}}'
```

## 3. Load knowledge into Qdrant

Run locally from the repo:

```bash
cd backend
pip install -r requirements-cloud.txt
python scripts/cloud_qdrant_fastembed_bootstrap.py --source data --collection ai_talapker_fastembed_384 --recreate --limit 300
```

Remove `--limit 300` for full load.

## 4. Backend service on Render

Create a Render Web Service:

```text
Runtime: Docker
Root Directory: backend
Dockerfile Path: ./Dockerfile
Docker Build Context Directory: .
Health Check Path: /health
```

Environment variables:

```env
APP_ENV=demo
APP_CONFIG_FILE=configs/app.cloud.yaml
APP_DISABLE_MODEL_LOAD=1
APP_USE_EXISTING_QDRANT_INDEX=1

LLM_PROVIDER=openrouter
APP_GENERATION_BACKEND=openrouter
EMBEDDING_PROVIDER=fastembed
APP_EMBEDDING_BACKEND=fastembed

OPENROUTER_API_KEY=...
OPENROUTER_CHAT_MODEL=meta-llama/llama-3.3-70b-instruct:free
OPENROUTER_HTTP_REFERER=https://YOUR-BACKEND.onrender.com
OPENROUTER_APP_TITLE=AI-Talapker Demo

FASTEMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
FASTEMBED_DIMENSIONS=384
FASTEMBED_CACHE_DIR=models/fastembed
FASTEMBED_THREADS=1
FASTEMBED_BATCH_SIZE=16

QDRANT_URL=https://YOUR-QDRANT-CLUSTER.cloud.qdrant.io:6333
QDRANT_API_KEY=...
QDRANT_COLLECTION=ai_talapker_fastembed_384
QDRANT_VECTOR_NAME=

ADMIN_JWT_SECRET=long_random_secret
ADMIN_ALLOW_CONFIG_BOOTSTRAP=1
ADMIN_USERNAME=main_admin
ADMIN_PASSWORD=change_this_password
AUTH_DB_PATH=/tmp/auth.db

APP_CORS_ORIGINS=https://n8n.kstu.kz,https://YOUR-FRONTEND.onrender.com
```

Do not set these in this version:

```env
OPENROUTER_EMBEDDING_MODEL
OPENROUTER_EMBEDDING_DIMENSIONS
```

Test:

```text
https://YOUR-BACKEND.onrender.com/health
https://YOUR-BACKEND.onrender.com/debug/rag?q=Какие образовательные программы есть?
```

`/health` should show `index_ready: true` and collection `ai_talapker_fastembed_384`.

## 5. Frontend service on Render

Create a second Render Web Service:

```text
Runtime: Docker
Root Directory: frontend
Dockerfile Path: ./Dockerfile
Docker Build Context Directory: .
```

Environment variable:

```env
BACKEND_API_BASE=https://YOUR-BACKEND.onrender.com
```

After frontend deploy, add the frontend URL to backend `APP_CORS_ORIGINS`, then redeploy backend.

## 6. n8n

Import:

```text
n8n/ai-talapker-n8n-telegram-render-backend-proxy.workflow.json
```

Set inside the Code node:

```js
const ASSISTANT_API_BASE = 'https://YOUR-BACKEND.onrender.com';
```

Telegram webhook points to n8n, not Render.
