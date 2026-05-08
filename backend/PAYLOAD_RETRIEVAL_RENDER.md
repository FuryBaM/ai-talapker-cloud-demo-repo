# Render Free payload retrieval

This mode is for Render Free 512MB deployments. It does not load FastEmbed or
call an embedding API at request time. The backend opens the existing Qdrant
collection and searches payload text with domain filters and keyword scoring.

Use it when the vector collection is already populated, but the web service
runs out of memory loading FastEmbed.

Render environment:

```env
APP_ENV=demo
APP_CONFIG_FILE=configs/app.cloud.yaml
APP_USE_EXISTING_QDRANT_INDEX=1
APP_DISABLE_LOCAL_MODEL_LOAD=1
APP_DISABLE_ALL_MODEL_LOAD=0

APP_GENERATION_BACKEND=openrouter
LLM_PROVIDER=openrouter
APP_EMBEDDING_BACKEND=payload
EMBEDDING_PROVIDER=payload

OPENROUTER_API_KEY=...
OPENROUTER_CHAT_MODEL=meta-llama/llama-3.3-70b-instruct:free

QDRANT_URL=https://YOUR-QDRANT-CLUSTER.cloud.qdrant.io:6333
QDRANT_API_KEY=...
QDRANT_COLLECTION=ai_talapker_fastembed_384
QDRANT_VECTOR_NAME=
```

No Qdrant reindex is required. `/health` should show:

```json
{
  "index_ready": true,
  "collection": "ai_talapker_fastembed_384",
  "qdrant_points": 516,
  "embedding_backend": "payload"
}
```

Tradeoff: retrieval is rougher than vector search, but it is free and stable
for demos with small collections.
