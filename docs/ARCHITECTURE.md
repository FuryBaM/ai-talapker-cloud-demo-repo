# Architecture

The cloud demo does not replace the backend with n8n. n8n is only the integration layer.

```text
Telegram update
  -> n8n webhook
  -> FastAPI /chat on Render
  -> LangGraph/RAG pipeline
  -> Qdrant Cloud retrieval
  -> OpenRouter chat completion
  -> FastAPI JSON answer
  -> n8n Telegram sendMessage
```

Provider mode:

```env
LLM_PROVIDER=openrouter
EMBEDDING_PROVIDER=openrouter
```

Local mode can still use local model providers. Cloud mode uses OpenRouter because Render Free cannot run GGUF/transformers models.

Important invariant:

```text
The embedding provider used for indexing and the embedding provider used for search must be the same.
```

For this cloud demo:

```text
OpenRouter text-embedding-3-small -> Qdrant collection size 1536
```
