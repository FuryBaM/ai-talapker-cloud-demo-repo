# AI-Talapker Render mode with FastEmbed

This mode removes OpenRouter embeddings from the deploy path.

Architecture:

```text
n8n / frontend -> Render FastAPI -> FastEmbed CPU embeddings -> Qdrant Cloud -> OpenRouter chat -> response
```

Use these Render environment variables:

```env
APP_ENV=demo
APP_CONFIG_FILE=configs/app.cloud.yaml

LLM_PROVIDER=openrouter
EMBEDDING_PROVIDER=fastembed
APP_GENERATION_BACKEND=openrouter
APP_EMBEDDING_BACKEND=fastembed

OPENROUTER_API_KEY=...
OPENROUTER_CHAT_MODEL=openrouter/free

FASTEMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
FASTEMBED_DIMENSIONS=384
FASTEMBED_CACHE_DIR=models/fastembed
FASTEMBED_THREADS=1
FASTEMBED_BATCH_SIZE=16

QDRANT_URL=https://YOUR-QDRANT-CLUSTER.cloud.qdrant.io
QDRANT_API_KEY=...
QDRANT_COLLECTION=ai_talapker_fastembed_384
QDRANT_VECTOR_NAME=

ADMIN_JWT_SECRET=CHANGE_THIS_LONG_RANDOM_SECRET
APP_CORS_ORIGINS=https://n8n.kstu.kz,https://YOUR-FRONTEND.onrender.com
```

Qdrant collection must use 384 dimensions:

```bash
curl -X PUT \
  -H "Content-Type: application/json" \
  -H "api-key: $QDRANT_API_KEY" \
  "$QDRANT_URL/collections/ai_talapker_fastembed_384" \
  -d '{"vectors":{"size":384,"distance":"Cosine"}}'
```

Index from local machine:

```bash
cd backend
python scripts/cloud_qdrant_fastembed_bootstrap.py --source data --collection ai_talapker_fastembed_384 --recreate --limit 300
```

Remove `--limit 300` for full indexing.

Important: use the same `FASTEMBED_MODEL` for indexing and for Render runtime.
