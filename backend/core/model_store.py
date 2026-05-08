from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import requests

from core.llama_server_runtime import build_server_settings, get_or_start_server
from core.config import (
    ANSWER_GUARD_ENABLED,
    APP_DEVICE,
    EMBED_BACKEND,
    EMBED_BATCH_SIZE,
    EMBED_DEVICE,
    EMBED_GGUF_DEVICE,
    EMBED_GGUF_MAIN_GPU,
    EMBED_GGUF_N_CTX,
    EMBED_GGUF_N_GPU_LAYERS,
    EMBED_GGUF_PATH,
    EMBED_MAX_SEQ_LENGTH,
    FASTEMBED_BATCH_SIZE,
    FASTEMBED_CACHE_DIR,
    FASTEMBED_DIMENSIONS,
    FASTEMBED_MODEL,
    FASTEMBED_THREADS,
    EMBED_MODEL_DIR,
    GEN_BACKEND,
    GEN_GGUF_CHAT_FORMAT,
    GEN_GGUF_DEVICE,
    GEN_GGUF_MAIN_GPU,
    GEN_GGUF_N_CTX,
    GEN_GGUF_N_GPU_LAYERS,
    GEN_GGUF_PATH,
    GEN_MODEL_DIR,
    GGUF_RUNTIME,
    GGUF_VERBOSE,
    GUARD_BACKEND,
    GUARD_GGUF_CHAT_FORMAT,
    GUARD_GGUF_DEVICE,
    GUARD_GGUF_MAIN_GPU,
    GUARD_GGUF_N_CTX,
    GUARD_GGUF_N_GPU_LAYERS,
    GUARD_GGUF_PATH,
    GUARD_MODEL_DIR,
    LOAD_4BIT,
    OPENROUTER_API_KEY,
    OPENROUTER_APP_TITLE,
    OPENROUTER_BASE_URL,
    OPENROUTER_CHAT_MODEL,
    OPENROUTER_EMBEDDING_DIMENSIONS,
    OPENROUTER_EMBEDDING_MODEL,
    OPENROUTER_GUARD_MODEL,
    OPENROUTER_HTTP_REFERER,
    OPENROUTER_TIMEOUT,
    TOKEN,
    TORCH_REQUIRED,
    USE_CUDA,
)


class ApproxTextTokenizer:
    """Small reversible tokenizer used when the embedding stack is GGUF-only.

    It is not a model tokenizer. It only gives the chunker a stable encode/decode
    interface without importing transformers/torch.
    """

    eos_token_id = 0
    pad_token_id = 0
    pad_token = "<pad>"
    eos_token = "<eos>"
    padding_side = "left"

    def __init__(self) -> None:
        self._token_to_id: dict[str, int] = {self.pad_token: 0}
        self._id_to_token: dict[int, str] = {0: ""}

    def _pieces(self, text: str) -> list[str]:
        return re.findall(r"\s+|\S+", str(text or ""), flags=re.UNICODE)

    def encode(
        self,
        text: str,
        add_special_tokens: bool = False,
        truncation: bool = False,
        max_length: int | None = None,
        **_: Any,
    ) -> list[int]:
        ids: list[int] = []
        for piece in self._pieces(text):
            token_id = self._token_to_id.get(piece)
            if token_id is None:
                token_id = len(self._token_to_id)
                self._token_to_id[piece] = token_id
                self._id_to_token[token_id] = piece
            ids.append(token_id)
        if truncation and max_length is not None:
            ids = ids[: max(0, int(max_length))]
        return ids

    def decode(self, token_ids: Iterable[int], skip_special_tokens: bool = True, **_: Any) -> str:
        return "".join(self._id_to_token.get(int(token_id), "") for token_id in token_ids)

    def convert_tokens_to_ids(self, token: str) -> int | None:
        return self._token_to_id.get(token)


class DisabledEmbeddingModel:
    """Tiny CI/smoke-test model used only when real model loading is explicitly disabled."""

    def get_sentence_embedding_dimension(self) -> int:
        return 8

    def encode(self, texts: Any, *args: Any, **kwargs: Any) -> Any:
        single = isinstance(texts, str)
        count = 1 if single else len(list(texts or []))
        vectors = np.zeros((count, 8), dtype=np.float32)
        if kwargs.get("convert_to_numpy"):
            return vectors[0] if single else vectors
        return vectors[0].tolist() if single else vectors.tolist()


class PayloadOnlyEmbeddingModel(DisabledEmbeddingModel):
    """Placeholder for Qdrant payload/keyword retrieval mode."""

    backend = "payload"


class DisabledChatModel:
    backend = "disabled"
    device = "disabled"

    def generate_chat(self, messages: list[dict], max_new_tokens: int, temperature: float = 0.0) -> str:
        return ""


class OpenRouterChatModel:
    """OpenAI-compatible chat provider adapter for cloud/demo deployments.

    It intentionally exposes the same generate_chat() method as the GGUF
    adapters, so the rest of the application does not care whether the
    model is local, llama-server, or OpenRouter.
    """

    backend = "openrouter"
    device = "api"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: int = 60,
        http_referer: str = "",
        app_title: str = "AI-Talapker",
    ) -> None:
        self.api_key = str(api_key or "").strip()
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required when generation backend is openrouter")
        self.model = str(model or "openrouter/free").strip()
        self.base_url = str(base_url or "https://openrouter.ai/api/v1").rstrip("/")
        self.timeout = int(timeout or 60)
        self.http_referer = str(http_referer or "").strip()
        self.app_title = str(app_title or "AI-Talapker").strip()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        if self.app_title:
            headers["X-Title"] = self.app_title
        return headers

    def generate_chat(self, messages: list[dict], max_new_tokens: int, temperature: float = 0.0) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": int(max_new_tokens),
            "temperature": float(temperature or 0.0),
        }
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"OpenRouter chat failed: HTTP {response.status_code}: {response.text[:1000]}")
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content") or "").strip()


class OpenRouterEmbeddingModel:
    """SentenceTransformer-compatible embedding adapter for OpenRouter."""

    backend = "openrouter"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        dimensions: int = 1536,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout: int = 60,
        http_referer: str = "",
        app_title: str = "AI-Talapker",
    ) -> None:
        self.api_key = str(api_key or "").strip()
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required when embedding backend is openrouter")
        self.model = str(model or "openai/text-embedding-3-small").strip()
        self.dimensions = int(dimensions or 0)
        self.base_url = str(base_url or "https://openrouter.ai/api/v1").rstrip("/")
        self.timeout = int(timeout or 60)
        self.http_referer = str(http_referer or "").strip()
        self.app_title = str(app_title or "AI-Talapker").strip()
        self.max_seq_length = 512
        self._dimension: int | None = self.dimensions if self.dimensions > 0 else None

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        if self.app_title:
            headers["X-Title"] = self.app_title
        return headers

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {"model": self.model, "input": texts}
        if self.dimensions > 0:
            payload["dimensions"] = self.dimensions
        response = requests.post(
            f"{self.base_url}/embeddings",
            headers=self._headers(),
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"OpenRouter embeddings failed: HTTP {response.status_code}: {response.text[:1000]}")
        data = response.json()
        items = data.get("data") or []
        if not isinstance(items, list):
            raise RuntimeError("OpenRouter returned an unexpected embeddings response shape")
        ordered = sorted(items, key=lambda item: int(item.get("index", 0)) if isinstance(item, dict) else 0)
        vectors = [list((item or {}).get("embedding") or []) for item in ordered]
        if not vectors and texts:
            raise RuntimeError("OpenRouter returned no embedding vectors")
        return vectors

    def get_sentence_embedding_dimension(self) -> int:
        if self._dimension is None:
            self._dimension = int(len(self._embed_batch(["dimension probe"])[0]))
        return int(self._dimension)

    def encode(
        self,
        sentences: str | list[str],
        convert_to_numpy: bool = True,
        normalize_embeddings: bool = True,
        batch_size: int = EMBED_BATCH_SIZE,
        show_progress_bar: bool = False,
        **_: Any,
    ):
        single = isinstance(sentences, str)
        items = [sentences] if single else list(sentences or [])
        batch = max(1, int(batch_size or EMBED_BATCH_SIZE or 1))
        vectors_list: list[list[float]] = []
        for start in range(0, len(items), batch):
            vectors_list.extend(self._embed_batch([str(item or "") for item in items[start : start + batch]]))
        vectors = np.array(vectors_list, dtype=np.float32)
        if normalize_embeddings and vectors.size:
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            vectors = vectors / norms
        if single:
            vectors = vectors[0]
        if convert_to_numpy:
            return vectors.astype(np.float32)
        return vectors.tolist()


class FastEmbedEmbeddingModel:
    """SentenceTransformer-compatible adapter over Qdrant FastEmbed.

    This backend is for free cloud demos: embeddings are generated on CPU in the
    FastAPI container through ONNX Runtime, without OpenRouter embedding credits
    and without PyTorch/sentence-transformers.
    """

    backend = "fastembed"

    def __init__(
        self,
        *,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        cache_dir: str = "",
        threads: int = 1,
        batch_size: int = 16,
        dimensions: int = 384,
    ) -> None:
        self.model_name = str(model_name or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2").strip()
        self.cache_dir = str(cache_dir or "").strip()
        self.threads = int(threads or 1)
        self.batch_size = max(1, int(batch_size or 16))
        self._dimension: int | None = int(dimensions or 0) or None
        self.max_seq_length = 512
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from fastembed import TextEmbedding  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "FastEmbed backend requires the 'fastembed' package. "
                "Install cloud requirements or run: pip install fastembed"
            ) from exc

        kwargs: dict[str, Any] = {"model_name": self.model_name}
        if self.cache_dir:
            Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
            kwargs["cache_dir"] = self.cache_dir
        if self.threads > 0:
            kwargs["threads"] = self.threads
        try:
            self._model = TextEmbedding(**kwargs)
        except TypeError:
            # Older fastembed versions have a narrower constructor.
            kwargs.pop("threads", None)
            self._model = TextEmbedding(**kwargs)
        return self._model

    def _infer_dimension_from_registry(self) -> int | None:
        try:
            from fastembed import TextEmbedding  # type: ignore

            for item in TextEmbedding.list_supported_models():
                if not isinstance(item, dict):
                    continue
                model = str(item.get("model") or item.get("model_name") or item.get("name") or "")
                if model != self.model_name:
                    continue
                for key in ("dim", "dimension", "dimensions", "size"):
                    value = item.get(key)
                    if isinstance(value, int) and value > 0:
                        return value
                    if isinstance(value, str) and value.isdigit():
                        return int(value)
        except Exception:
            return None
        return None

    def get_sentence_embedding_dimension(self) -> int:
        if self._dimension is None:
            self._dimension = self._infer_dimension_from_registry()
        if self._dimension is None:
            self._dimension = int(len(self.encode("dimension probe", convert_to_numpy=False)))
        return int(self._dimension)

    def _embed_items(self, items: list[str], batch_size: int) -> list[list[float]]:
        model = self._load()
        try:
            generated = model.embed(items, batch_size=max(1, int(batch_size or self.batch_size)))
        except TypeError:
            generated = model.embed(items)
        vectors = []
        for vector in generated:
            if hasattr(vector, "tolist"):
                vectors.append(vector.tolist())
            else:
                vectors.append(list(vector))
        return vectors

    def encode(
        self,
        sentences: str | list[str],
        convert_to_numpy: bool = True,
        normalize_embeddings: bool = True,
        batch_size: int = EMBED_BATCH_SIZE,
        show_progress_bar: bool = False,
        **_: Any,
    ):
        single = isinstance(sentences, str)
        items = [sentences] if single else list(sentences or [])
        safe_batch = max(1, int(batch_size or self.batch_size or 1))
        vectors_list: list[list[float]] = []
        for start in range(0, len(items), safe_batch):
            batch = [str(item or "") for item in items[start : start + safe_batch]]
            vectors_list.extend(self._embed_items(batch, safe_batch))
        vectors = np.array(vectors_list, dtype=np.float32)
        if normalize_embeddings and vectors.size:
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            vectors = vectors / norms
        if single:
            vectors = vectors[0]
        if convert_to_numpy:
            return vectors.astype(np.float32)
        return vectors.tolist()


def _require_file(path: str, *, label: str) -> str:
    if not path:
        raise RuntimeError(f"{label} is configured as GGUF, but no .gguf path is set in configs/app.yaml")
    resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"{label} GGUF file not found: {resolved}")
    return str(resolved)


def _import_llama_cpp():
    try:
        from llama_cpp import Llama  # type: ignore

        return Llama
    except ImportError as exc:
        raise RuntimeError(
            "GGUF backend requires llama-cpp-python. Install it with: pip install llama-cpp-python"
        ) from exc


class LlamaCppChatModel:
    backend = "gguf"
    device = "gguf"

    def __init__(
        self,
        model_path: str,
        *,
        n_ctx: int = 8192,
        n_gpu_layers: int = -1,
        chat_format: str | None = None,
        verbose: bool = False,
        label: str = "generation",
    ) -> None:
        Llama = _import_llama_cpp()
        kwargs: dict[str, Any] = {
            "model_path": _require_file(model_path, label=label),
            "n_ctx": int(n_ctx),
            "n_gpu_layers": int(n_gpu_layers),
            "verbose": bool(verbose),
        }
        if chat_format:
            kwargs["chat_format"] = chat_format
        self.llm = Llama(**kwargs)

    def generate_chat(self, messages: list[dict], max_new_tokens: int, temperature: float = 0.0) -> str:
        response = self.llm.create_chat_completion(
            messages=messages,
            max_tokens=int(max_new_tokens),
            temperature=float(temperature),
            top_p=1.0,
        )
        choices = response.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content") or "").strip()


class LlamaCppEmbeddingModel:
    backend = "gguf"

    def __init__(
        self,
        model_path: str,
        *,
        n_ctx: int = 512,
        n_gpu_layers: int = -1,
        verbose: bool = False,
    ) -> None:
        Llama = _import_llama_cpp()
        self.llm = Llama(
            model_path=_require_file(model_path, label="embedding"),
            embedding=True,
            n_ctx=int(n_ctx),
            n_gpu_layers=int(n_gpu_layers),
            verbose=bool(verbose),
        )
        self.max_seq_length = int(n_ctx)
        self._dimension: int | None = None

    def _embed_one(self, text: str) -> list[float]:
        response = self.llm.create_embedding(str(text or ""))
        data = response.get("data") or []
        if data and isinstance(data[0], dict):
            return list(data[0].get("embedding") or [])
        if "embedding" in response:
            return list(response.get("embedding") or [])
        raise RuntimeError("llama.cpp returned an unexpected embedding response shape")

    def get_sentence_embedding_dimension(self) -> int:
        if self._dimension is None:
            self._dimension = int(len(self._embed_one("dimension probe")))
        return self._dimension

    def encode(
        self,
        sentences: str | list[str],
        convert_to_numpy: bool = True,
        normalize_embeddings: bool = True,
        batch_size: int = EMBED_BATCH_SIZE,
        show_progress_bar: bool = False,
        **_: Any,
    ):
        single = isinstance(sentences, str)
        items = [sentences] if single else list(sentences or [])
        vectors = np.array([self._embed_one(item) for item in items], dtype=np.float32)
        if normalize_embeddings and vectors.size:
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            vectors = vectors / norms
        if single:
            vectors = vectors[0]
        if convert_to_numpy:
            return vectors.astype(np.float32)
        return vectors.tolist()


class LlamaServerChatModel:
    backend = "llama_server"
    device = "llama_server"

    def __init__(
        self,
        model_path: str,
        *,
        name: str = "generation",
        n_ctx: int = 8192,
        n_gpu_layers: int = -1,
        main_gpu: int = 0,
        device: str = "auto",
        label: str = "generation",
    ) -> None:
        settings = build_server_settings(
            name=name,
            model_path=_require_file(model_path, label=label),
            purpose="chat",
            n_ctx=int(n_ctx),
            n_gpu_layers=int(n_gpu_layers),
            main_gpu=int(main_gpu),
            device=device,
            embedding=False,
        )
        self.server = get_or_start_server(settings)
        self.base_url = settings.base_url.rstrip("/")
        self.model = "local"
        self.n_ctx = int(settings.n_ctx)

    def _clip_messages_for_context(
        self,
        messages: list[dict],
        max_new_tokens: int,
        margin_tokens: int = 96,
        n_ctx_override: int | None = None,
    ) -> list[dict]:
        """Approximate prompt trimming before sending to llama-server.

        llama-server validates prompt tokens against the server's real n_ctx. This
        method is intentionally conservative because we do not have the exact
        llama.cpp tokenizer in Python. It prevents compact helper/final prompts
        from crashing the graph when a stale or smaller local server is running.
        """
        ctx_limit = max(512, int(n_ctx_override or self.n_ctx or 2048))
        max_out = max(64, int(max_new_tokens or 0))
        # Keep a real answer budget, but do not let it consume the whole context.
        max_out = min(max_out, max(128, ctx_limit // 3))
        budget_tokens = max(128, ctx_limit - max_out - int(margin_tokens))
        # Russian/Kazakh text can tokenize densely. 2 chars/token is conservative.
        budget_chars = max(700, budget_tokens * 2)

        normalized: list[dict] = []
        for msg in messages:
            content = str(msg.get("content") or "")
            role = str(msg.get("role") or "user")
            if role == "system" and len(content) > 900:
                content = content[:900].rstrip() + "\n...[system prompt truncated]"
            normalized.append({**msg, "role": role, "content": content})

        total_chars = sum(len(str(msg.get("content") or "")) for msg in normalized)
        if total_chars <= budget_chars:
            return normalized

        overflow = total_chars - budget_chars
        clipped = list(normalized)
        for idx in sorted(range(len(clipped)), key=lambda i: len(str(clipped[i].get("content") or "")), reverse=True):
            if overflow <= 0:
                break
            content = str(clipped[idx].get("content") or "")
            role = clipped[idx].get("role")
            min_keep = 350 if role != "system" else 500
            if len(content) <= min_keep:
                continue
            remove = min(overflow + 250, len(content) - min_keep)
            keep = len(content) - remove
            if role == "system":
                new_content = content[:keep].rstrip() + "\n...[truncated]"
            else:
                # Preserve the beginning and the end: context usually starts with
                # instructions and ends with the actual user question.
                head_keep = min(250, max(100, keep // 4))
                tail_keep = max(250, keep - head_keep)
                new_content = (
                    content[:head_keep].rstrip()
                    + "\n...[prompt truncated to fit local llama-server context]...\n"
                    + content[-tail_keep:].lstrip()
                )
            overflow -= len(content) - len(new_content)
            clipped[idx] = {**clipped[idx], "content": new_content}
        return clipped

    @staticmethod
    def _extract_context_error(response: requests.Response) -> tuple[int | None, str]:
        try:
            payload = response.json()
        except Exception:
            return None, response.text
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return None, response.text
        n_ctx = error.get("n_ctx")
        try:
            parsed_ctx = int(n_ctx) if n_ctx is not None else None
        except Exception:
            parsed_ctx = None
        return parsed_ctx, str(error.get("message") or response.text)

    def generate_chat(self, messages: list[dict], max_new_tokens: int, temperature: float = 0.0) -> str:
        requested_max = max(64, int(max_new_tokens or 0))
        effective_max = min(requested_max, max(128, int(self.n_ctx or 2048) // 3))
        payload_messages = self._clip_messages_for_context(messages, max_new_tokens=effective_max)
        request_payload = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": float(temperature),
            "max_tokens": int(effective_max),
            # Required for Qwen3.5 GGUF; otherwise llama-server may return empty
            # message.content and put text into reasoning_content.
            "chat_template_kwargs": {"enable_thinking": False},
        }

        response = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json=request_payload,
            timeout=240,
        )

        if response.status_code == 400 and "exceeds the available context size" in response.text:
            server_ctx, _ = self._extract_context_error(response)
            server_ctx = int(server_ctx or self.n_ctx or 2048)
            # The server's actual n_ctx is authoritative. This also handles stale
            # servers that were started manually with a smaller -c value.
            retry_max = min(effective_max, max(96, server_ctx // 4))
            retry_messages = self._clip_messages_for_context(
                messages,
                max_new_tokens=retry_max,
                margin_tokens=512,
                n_ctx_override=server_ctx,
            )
            request_payload["messages"] = retry_messages
            request_payload["max_tokens"] = int(retry_max)
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json=request_payload,
                timeout=240,
            )

        if response.status_code >= 400:
            raise RuntimeError(f"llama-server chat failed: HTTP {response.status_code}: {response.text[:1000]}")
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = str(message.get("content") or "").strip()
        if not content and message.get("reasoning_content"):
            # Do not leak chain-of-thought. This message points at the real fix.
            raise RuntimeError("llama-server returned empty content and non-empty reasoning_content; enable_thinking=false is not applied")
        return content


class LlamaServerEmbeddingModel:
    backend = "llama_server"

    def __init__(
        self,
        model_path: str,
        *,
        n_ctx: int = 512,
        n_gpu_layers: int = -1,
        main_gpu: int = 0,
        device: str = "auto",
    ) -> None:
        settings = build_server_settings(
            name="embedding",
            model_path=_require_file(model_path, label="embedding"),
            purpose="embedding",
            n_ctx=int(n_ctx),
            n_gpu_layers=int(n_gpu_layers),
            main_gpu=int(main_gpu),
            device=device,
            embedding=True,
        )
        self.server = get_or_start_server(settings)
        self.base_url = settings.base_url.rstrip("/")
        self.model = "local"
        self.max_seq_length = int(n_ctx)
        self._dimension: int | None = None

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = requests.post(
            f"{self.base_url}/v1/embeddings",
            json={"model": self.model, "input": texts},
            timeout=240,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"llama-server embedding failed: HTTP {response.status_code}: {response.text[:1000]}")
        payload = response.json()
        data = payload.get("data") or []
        if not isinstance(data, list):
            raise RuntimeError("llama-server returned an unexpected embeddings response shape")
        ordered = sorted(data, key=lambda item: int(item.get("index", 0)) if isinstance(item, dict) else 0)
        vectors = [list((item or {}).get("embedding") or []) for item in ordered]
        if not vectors and texts:
            raise RuntimeError("llama-server returned no embedding vectors")
        return vectors

    def get_sentence_embedding_dimension(self) -> int:
        if self._dimension is None:
            self._dimension = int(len(self._embed_batch(["dimension probe"])[0]))
        return self._dimension

    def encode(
        self,
        sentences: str | list[str],
        convert_to_numpy: bool = True,
        normalize_embeddings: bool = True,
        batch_size: int = EMBED_BATCH_SIZE,
        show_progress_bar: bool = False,
        **_: Any,
    ):
        single = isinstance(sentences, str)
        items = [sentences] if single else list(sentences or [])
        batch = max(1, int(batch_size or EMBED_BATCH_SIZE or 1))
        vectors_list: list[list[float]] = []
        for start in range(0, len(items), batch):
            vectors_list.extend(self._embed_batch([str(item or "") for item in items[start : start + batch]]))
        vectors = np.array(vectors_list, dtype=np.float32)
        if normalize_embeddings and vectors.size:
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            vectors = vectors / norms
        if single:
            vectors = vectors[0]
        if convert_to_numpy:
            return vectors.astype(np.float32)
        return vectors.tolist()


def _import_torch_stack():
    try:
        import torch  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig  # type: ignore

        return torch, AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    except ImportError as exc:
        raise RuntimeError(
            "Transformers/SentenceTransformer backend requires torch, transformers and sentence-transformers. "
            "Use GGUF for all configured models or install the torch-based requirements."
        ) from exc


def _configure_torch() -> Any | None:
    if not TORCH_REQUIRED:
        return None
    torch, _, _, _ = _import_torch_stack()
    try:
        torch.backends.cuda.matmul.allow_tf32 = True
    except Exception:
        pass
    try:
        torch.set_float32_matmul_precision("high")
    except Exception:
        pass
    return torch


torch = _configure_torch()


def _load_embedding_pair():
    if EMBED_BACKEND == "payload":
        return ApproxTextTokenizer(), PayloadOnlyEmbeddingModel()
    if EMBED_BACKEND == "fastembed":
        return ApproxTextTokenizer(), FastEmbedEmbeddingModel(
            model_name=FASTEMBED_MODEL,
            cache_dir=FASTEMBED_CACHE_DIR,
            threads=FASTEMBED_THREADS,
            batch_size=FASTEMBED_BATCH_SIZE,
            dimensions=FASTEMBED_DIMENSIONS,
        )
    if EMBED_BACKEND == "openrouter":
        return ApproxTextTokenizer(), OpenRouterEmbeddingModel(
            api_key=OPENROUTER_API_KEY,
            model=OPENROUTER_EMBEDDING_MODEL,
            dimensions=OPENROUTER_EMBEDDING_DIMENSIONS,
            base_url=OPENROUTER_BASE_URL,
            timeout=OPENROUTER_TIMEOUT,
            http_referer=OPENROUTER_HTTP_REFERER,
            app_title=OPENROUTER_APP_TITLE,
        )
    if EMBED_BACKEND in {"gguf", "llama_server"}:
        if GGUF_RUNTIME == "llama_server":
            return ApproxTextTokenizer(), LlamaServerEmbeddingModel(
                EMBED_GGUF_PATH,
                n_ctx=EMBED_GGUF_N_CTX,
                n_gpu_layers=EMBED_GGUF_N_GPU_LAYERS,
                main_gpu=EMBED_GGUF_MAIN_GPU,
                device=EMBED_GGUF_DEVICE,
            )
        return ApproxTextTokenizer(), LlamaCppEmbeddingModel(
            EMBED_GGUF_PATH,
            n_ctx=EMBED_GGUF_N_CTX,
            n_gpu_layers=EMBED_GGUF_N_GPU_LAYERS,
            verbose=GGUF_VERBOSE,
        )
    if EMBED_BACKEND != "sentence_transformers":
        raise RuntimeError(f"Unsupported embedding backend: {EMBED_BACKEND}")

    from sentence_transformers import SentenceTransformer  # type: ignore
    from transformers import AutoTokenizer  # type: ignore

    tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL_DIR)
    model = SentenceTransformer(EMBED_MODEL_DIR, device=EMBED_DEVICE)
    if EMBED_MAX_SEQ_LENGTH > 0:
        model.max_seq_length = EMBED_MAX_SEQ_LENGTH
    return tokenizer, model


def _causal_model_kwargs() -> dict:
    if torch is None:
        raise RuntimeError("Torch backend requested, but torch is not loaded")
    _, _, _, BitsAndBytesConfig = _import_torch_stack()
    kwargs: dict[str, Any] = {}
    if TOKEN:
        kwargs["token"] = TOKEN
    if USE_CUDA:
        kwargs["device_map"] = APP_DEVICE
        if LOAD_4BIT:
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
            )
        else:
            kwargs["dtype"] = torch.float16
    else:
        kwargs["dtype"] = torch.float32
    return kwargs


def _load_transformers_causal_pair(model_dir: str):
    if torch is None:
        raise RuntimeError("Transformers generation requires torch")
    _, AutoModelForCausalLM, AutoTokenizer, _ = _import_torch_stack()
    model = AutoModelForCausalLM.from_pretrained(model_dir, **_causal_model_kwargs())
    if not USE_CUDA:
        model.to(APP_DEVICE)

    tokenizer_kwargs = {}
    if TOKEN:
        tokenizer_kwargs["token"] = TOKEN
    tokenizer = AutoTokenizer.from_pretrained(model_dir, **tokenizer_kwargs)
    tokenizer.padding_side = "left"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    im_end = tokenizer.convert_tokens_to_ids("<|im_end|>")
    return model, tokenizer, im_end


def _load_generation_pair():
    if GEN_BACKEND == "openrouter":
        return (
            OpenRouterChatModel(
                api_key=OPENROUTER_API_KEY,
                model=OPENROUTER_CHAT_MODEL,
                base_url=OPENROUTER_BASE_URL,
                timeout=OPENROUTER_TIMEOUT,
                http_referer=OPENROUTER_HTTP_REFERER,
                app_title=OPENROUTER_APP_TITLE,
            ),
            None,
            None,
        )
    if GEN_BACKEND in {"gguf", "llama_server"}:
        if GGUF_RUNTIME == "llama_server":
            return (
                LlamaServerChatModel(
                    GEN_GGUF_PATH,
                    name="generation",
                    n_ctx=GEN_GGUF_N_CTX,
                    n_gpu_layers=GEN_GGUF_N_GPU_LAYERS,
                    main_gpu=GEN_GGUF_MAIN_GPU,
                    device=GEN_GGUF_DEVICE,
                    label="generation",
                ),
                None,
                None,
            )
        return (
            LlamaCppChatModel(
                GEN_GGUF_PATH,
                n_ctx=GEN_GGUF_N_CTX,
                n_gpu_layers=GEN_GGUF_N_GPU_LAYERS,
                chat_format=GEN_GGUF_CHAT_FORMAT,
                verbose=GGUF_VERBOSE,
                label="generation",
            ),
            None,
            None,
        )
    if GEN_BACKEND == "transformers":
        return _load_transformers_causal_pair(GEN_MODEL_DIR)
    raise RuntimeError(f"Unsupported generation backend: {GEN_BACKEND}")


def _load_guard_pair():
    if not ANSWER_GUARD_ENABLED:
        return None, None, None
    if GUARD_BACKEND == "openrouter":
        return (
            OpenRouterChatModel(
                api_key=OPENROUTER_API_KEY,
                model=OPENROUTER_GUARD_MODEL or OPENROUTER_CHAT_MODEL,
                base_url=OPENROUTER_BASE_URL,
                timeout=OPENROUTER_TIMEOUT,
                http_referer=OPENROUTER_HTTP_REFERER,
                app_title=OPENROUTER_APP_TITLE,
            ),
            None,
            None,
        )
    if GUARD_BACKEND in {"gguf", "llama_server"}:
        if GGUF_RUNTIME == "llama_server":
            return (
                LlamaServerChatModel(
                    GUARD_GGUF_PATH,
                    name="guard",
                    n_ctx=GUARD_GGUF_N_CTX,
                    n_gpu_layers=GUARD_GGUF_N_GPU_LAYERS,
                    main_gpu=GUARD_GGUF_MAIN_GPU,
                    device=GUARD_GGUF_DEVICE,
                    label="guard",
                ),
                None,
                None,
            )
        return (
            LlamaCppChatModel(
                GUARD_GGUF_PATH,
                n_ctx=GUARD_GGUF_N_CTX,
                n_gpu_layers=GUARD_GGUF_N_GPU_LAYERS,
                chat_format=GUARD_GGUF_CHAT_FORMAT,
                verbose=GGUF_VERBOSE,
                label="guard",
            ),
            None,
            None,
        )
    if GUARD_BACKEND == "transformers":
        return _load_transformers_causal_pair(GUARD_MODEL_DIR)
    raise RuntimeError(f"Unsupported guard backend: {GUARD_BACKEND}")


def _truthy_env(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


_CLOUD_SAFE_EMBED_BACKENDS = {"fastembed", "openrouter", "payload"}
_CLOUD_SAFE_CHAT_BACKENDS = {"openrouter"}
_DISABLE_LOCAL_MODEL_LOAD = _truthy_env("APP_DISABLE_MODEL_LOAD") or _truthy_env("APP_DISABLE_LOCAL_MODEL_LOAD")
_DISABLE_ALL_MODEL_LOAD = _truthy_env("APP_DISABLE_ALL_MODEL_LOAD")


if _DISABLE_ALL_MODEL_LOAD:
    # Explicit CI/smoke-test mode: no external API clients and no embedding model.
    embed_tokenizer, embed_model = ApproxTextTokenizer(), DisabledEmbeddingModel()
    gen_model, gen_tokenizer, im_end_token_id = DisabledChatModel(), None, None
    guard_model, guard_tokenizer, guard_im_end_token_id = None, None, None
else:
    # Render/cloud mode must still load cloud-safe backends. The old
    # APP_DISABLE_MODEL_LOAD=1 flag used to replace FastEmbed with an
    # 8-dimensional dummy model, which breaks retrieval against the
    # ai_talapker_fastembed_384 Qdrant collection.
    if _DISABLE_LOCAL_MODEL_LOAD and EMBED_BACKEND not in _CLOUD_SAFE_EMBED_BACKENDS:
        embed_tokenizer, embed_model = ApproxTextTokenizer(), DisabledEmbeddingModel()
    else:
        embed_tokenizer, embed_model = _load_embedding_pair()

    if _DISABLE_LOCAL_MODEL_LOAD and GEN_BACKEND not in _CLOUD_SAFE_CHAT_BACKENDS:
        gen_model, gen_tokenizer, im_end_token_id = DisabledChatModel(), None, None
    else:
        gen_model, gen_tokenizer, im_end_token_id = _load_generation_pair()

    if not ANSWER_GUARD_ENABLED:
        guard_model, guard_tokenizer, guard_im_end_token_id = None, None, None
    elif _DISABLE_LOCAL_MODEL_LOAD and GUARD_BACKEND not in _CLOUD_SAFE_CHAT_BACKENDS:
        guard_model, guard_tokenizer, guard_im_end_token_id = None, None, None
    else:
        guard_model, guard_tokenizer, guard_im_end_token_id = _load_guard_pair()
