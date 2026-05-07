# AI-Talapker backend: Render + OpenRouter + Qdrant Cloud + n8n

This variant keeps the existing FastAPI backend and admin panel, but switches model providers through a factory-style backend adapter.

Unchanged project paths:

```text
models/
data/
input_data/
storage/
```

## Runtime modes

Local laptop / GGUF or transformers:

```env
APP_CONFIG_FILE=configs/app.gguf.yaml
APP_GENERATION_BACKEND=llama_server
APP_EMBEDDING_BACKEND=llama_server
QDRANT_URL=
QDRANT_PATH=storage/qdrant
```

Render / cloud demo:

```env
APP_CONFIG_FILE=configs/app.cloud.yaml
LLM_PROVIDER=openrouter
EMBEDDING_PROVIDER=openrouter
OPENROUTER_API_KEY=...
QDRANT_URL=https://...cloud.qdrant.io
QDRANT_API_KEY=...
QDRANT_COLLECTION=ai_talapker_openrouter_1536
```

`LLM_PROVIDER` and `EMBEDDING_PROVIDER` are aliases for `APP_GENERATION_BACKEND` and `APP_EMBEDDING_BACKEND`. The backend currently supports:

```text
generation: transformers, gguf, llama_server, openrouter
embedding: sentence_transformers, gguf, llama_server, openrouter
```

## Qdrant collection rule

The embedding provider and the Qdrant collection must match.

For `openai/text-embedding-3-small` with `1536` dimensions:

```bash
curl -X PUT \
  -H "Content-Type: application/json" \
  -H "api-key: $QDRANT_API_KEY" \
  "$QDRANT_URL/collections/ai_talapker_openrouter_1536" \
  -d '{"vectors":{"size":1536,"distance":"Cosine"}}'
```

Do not use a collection indexed by local Qwen embeddings with OpenRouter query embeddings.

## Render deploy

Use `render.yaml` or create a Render Web Service manually:

```bash
pip install --upgrade pip && pip install -r requirements-cloud.txt
```

Start command:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Required environment variables:

```env
APP_CONFIG_FILE=configs/app.cloud.yaml
OPENROUTER_API_KEY=...
QDRANT_URL=...
QDRANT_API_KEY=...
QDRANT_COLLECTION=ai_talapker_openrouter_1536
ADMIN_JWT_SECRET=...
```

## n8n workflow shape

Keep n8n as Telegram integration layer:

```text
Telegram → n8n webhook → POST https://<render-app>.onrender.com/chat → n8n → Telegram sendMessage
```

Request body to `/chat`:

```json
{
  "session_id": "telegram:123456",
  "message": "Какие документы нужны для поступления?",
  "lang": "ru",
  "use_llm": true,
  "allow_web_search": false
}
```

Expected response:

```json
{
  "session_id": "telegram:123456",
  "answer": "...",
  "route": "knowledge",
  "profile_complete": false
}
```

## Notes

Render free is suitable for the FastAPI orchestration layer only. It will not run local Qwen/torch/GGUF models. Use OpenRouter or another external LLM provider in cloud mode.
