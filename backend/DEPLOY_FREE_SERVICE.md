# AI-Talapker: free-cloud demo deploy

Цель: показать Telegram + n8n + FastAPI/LangGraph backend + Qdrant + OpenRouter без ноутбука.

Схема:

```text
Telegram
→ n8n.kstu.kz webhook
→ Render/Koyeb FastAPI backend /chat
→ Qdrant Cloud retrieval
→ OpenRouter chat completion
→ n8n
→ Telegram sendMessage
```

n8n не заменяет backend. n8n только принимает Telegram update, вызывает `/chat` и отправляет ответ обратно в Telegram.

## 1. Что использовать бесплатно

Рекомендуемый вариант:

```text
FastAPI backend: Render Free Web Service или Koyeb Free Instance
Vector DB: Qdrant Cloud Free
LLM: OpenRouter free models
Telegram: Telegram Bot API
n8n: n8n.kstu.kz
```

Ограничение: free-сервисы годятся для демонстрации, не для production. Локальные Qwen GGUF/torch-модели на Render/Koyeb Free не запускать. В cloud-режиме используются OpenRouter и Qdrant Cloud.

## 2. Режимы backend

Локально на ноутбуке:

```env
APP_CONFIG_FILE=configs/app.gguf.yaml
APP_GENERATION_BACKEND=llama_server
APP_EMBEDDING_BACKEND=llama_server
```

Cloud demo:

```env
APP_CONFIG_FILE=configs/app.cloud.yaml
LLM_PROVIDER=openrouter
EMBEDDING_PROVIDER=openrouter
APP_GENERATION_BACKEND=openrouter
APP_EMBEDDING_BACKEND=openrouter
```

Пути `models`, `data`, `input_data` не меняются.

## 3. Render deploy

Вариант A: через `render.yaml`.

1. Залить проект в GitHub.
2. В Render выбрать **New → Blueprint**.
3. Подключить repo.
4. Render прочитает `assistant/render.yaml`.
5. Добавить env-переменные вручную.

Вариант B: вручную как Web Service.

Root directory:

```text
assistant
```

Build command:

```bash
pip install --upgrade pip && pip install -r requirements-cloud.txt
```

Start command:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

После деплоя проверить:

```bash
curl https://YOUR-SERVICE.onrender.com/health
```

## 4. Koyeb deploy

Koyeb можно запускать через Dockerfile или Python buildpack.

Root directory:

```text
assistant
```

Run command:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Build/install command:

```bash
pip install --upgrade pip && pip install -r requirements-cloud.txt
```

Env такие же, как для Render.

## 5. Env для cloud backend

Минимальный набор:

```env
APP_ENV=demo
APP_CONFIG_FILE=configs/app.cloud.yaml
APP_DISABLE_MODEL_LOAD=0
APP_DISABLE_RATE_LIMIT=0
APP_CORS_ORIGINS=*
APP_ALLOW_WILDCARD_CORS=1

LLM_PROVIDER=openrouter
EMBEDDING_PROVIDER=openrouter
APP_GENERATION_BACKEND=openrouter
APP_EMBEDDING_BACKEND=openrouter

OPENROUTER_API_KEY=put_key_here
OPENROUTER_CHAT_MODEL=openrouter/free
OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
OPENROUTER_EMBEDDING_DIMENSIONS=1536

QDRANT_URL=https://xxxxx.region.cloud.qdrant.io
QDRANT_API_KEY=put_qdrant_key_here
QDRANT_COLLECTION=ai_talapker_openrouter_1536
QDRANT_VECTOR_NAME=

ADMIN_JWT_SECRET=change_this_long_random_string
ADMIN_BOOTSTRAP_USERNAME=admin
ADMIN_BOOTSTRAP_PASSWORD=change_this_password
```

## 6. Создать Qdrant collection

Если используешь `openai/text-embedding-3-small` и `1536`, коллекция должна быть 1536 dimensions.

```bash
curl -X PUT \
  -H "Content-Type: application/json" \
  -H "api-key: $QDRANT_API_KEY" \
  "$QDRANT_URL/collections/ai_talapker_openrouter_1536" \
  -d '{"vectors":{"size":1536,"distance":"Cosine"}}'
```

Проверка:

```bash
curl -H "api-key: $QDRANT_API_KEY" "$QDRANT_URL/collections"
```

## 7. Загрузить базу знаний в Qdrant Cloud

Запускай локально из папки `assistant`, чтобы не тратить ресурсы free backend:

```bash
pip install httpx
python scripts/cloud_qdrant_bootstrap.py --source data --collection ai_talapker_openrouter_1536 --recreate
```

Для быстрого теста на 50 chunks:

```bash
python scripts/cloud_qdrant_bootstrap.py --source data --collection ai_talapker_openrouter_1536 --recreate --limit 50
```

Если уже есть готовый `storage/rag_chunks.jsonl`, лучше индексировать его:

```bash
python scripts/cloud_qdrant_bootstrap.py --source storage/rag_chunks.jsonl --collection ai_talapker_openrouter_1536 --recreate
```

Важно: embedding provider при загрузке и при поиске должен быть один и тот же. Если Qdrant загружен через OpenRouter `text-embedding-3-small`, backend тоже должен искать через OpenRouter `text-embedding-3-small`.

## 8. n8n workflow

Импортировать файл:

```text
n8n/ai-talapker-n8n-telegram-render-backend-proxy.workflow.json
```

В Code node заменить:

```js
const TELEGRAM_BOT_TOKEN = 'PUT_TELEGRAM_BOT_TOKEN_HERE';
const ASSISTANT_API_BASE = 'https://YOUR-SERVICE.onrender.com';
```

Активировать workflow.

Production webhook должен быть примерно:

```text
https://n8n.kstu.kz/webhook/ai-talapker-telegram-render-backend
```

Поставить webhook Telegram:

```bash
curl "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://n8n.kstu.kz/webhook/ai-talapker-telegram-render-backend"
```

## 9. Проверка

Проверять не small talk, а вопросы по базе:

```text
Какие документы нужны для поступления?
```

```text
Какие программы подходят если профильные математика и информатика?
```

```text
Что нужно для общежития?
```

Если ответ:

```text
There is not enough information in the database to answer this question.
```

значит одно из трех:

```text
1. Qdrant collection пустая.
2. Backend смотрит не в ту collection.
3. Embedding model при загрузке и поиске не совпадает.
```

## 10. Что говорить на демонстрации

```text
n8n используется как интеграционный слой для Telegram и автоматизации.
FastAPI/LangGraph backend содержит доменную AI-логику.
Provider factory позволяет запускать один backend в двух режимах: локально через Qwen/GGUF и в облаке через OpenRouter.
Qdrant Cloud хранит базу знаний.
Бесплатная инфраструктура используется только для демонстрации; для production нужен стабильный сервер/API budget/GPU.
```
