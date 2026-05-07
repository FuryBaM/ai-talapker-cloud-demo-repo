#!/usr/bin/env python3
"""
Bootstrap Qdrant Cloud for AI-Talapker using local FastEmbed CPU embeddings.

This script does not call OpenRouter embeddings and does not require PyTorch.
It uses the same FastEmbed model as the Render backend, then upserts vectors
and payload into Qdrant Cloud.

Required env:
  QDRANT_URL
  QDRANT_API_KEY

Optional env:
  FASTEMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
  FASTEMBED_DIMENSIONS=384
  FASTEMBED_CACHE_DIR=models/fastembed
  FASTEMBED_THREADS=1
  FASTEMBED_BATCH_SIZE=16
  QDRANT_COLLECTION=ai_talapker_fastembed_384
  QDRANT_VECTOR_NAME=
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

import httpx
import numpy as np

TEXT_KEYS = ("text", "raw_text", "content", "page_content", "chunk", "body", "embedding_text")
SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".csv"}
SUPPORTED_JSON_EXTENSIONS = {".json", ".jsonl"}


def load_dotenv_like(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env_str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def stable_id(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-talapker-fastembed:{digest}"))


def extract_text_from_record(record: dict[str, Any]) -> str:
    for key in TEXT_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_ws(text: str) -> str:
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    kept: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank:
                kept.append("")
            blank = True
            continue
        kept.append(line)
        blank = False
    return "\n".join(kept).strip()


def split_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    text = normalize_ws(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    current = ""
    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            parts.append(current)
            tail = current[-overlap_chars:] if overlap_chars > 0 else ""
            current = f"{tail}\n\n{paragraph}".strip() if tail else paragraph
        else:
            start = 0
            while start < len(paragraph):
                end = min(len(paragraph), start + max_chars)
                parts.append(paragraph[start:end].strip())
                if end >= len(paragraph):
                    break
                start = max(0, end - overlap_chars)
            current = ""
    if current:
        parts.append(current)
    return [p for p in parts if p.strip()]


def iter_json_records(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payload.setdefault("source_file", str(path))
                payload.setdefault("source_line", line_no)
                yield payload
        return

    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return
    if isinstance(payload, dict):
        for key in ("chunks", "records", "items", "documents", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        item.setdefault("source_file", str(path))
                        yield item
                return
        payload.setdefault("source_file", str(path))
        yield payload
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                item.setdefault("source_file", str(path))
                yield item


def collect_chunks(source: Path, *, max_chars: int, overlap_chars: int, limit: int | None) -> list[dict[str, Any]]:
    source = source.resolve()
    files: list[Path]
    if source.is_file():
        files = [source]
    else:
        files = [p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in (SUPPORTED_TEXT_EXTENSIONS | SUPPORTED_JSON_EXTENSIONS)]

    chunks: list[dict[str, Any]] = []
    for file_path in sorted(files):
        suffix = file_path.suffix.lower()
        if suffix in SUPPORTED_JSON_EXTENSIONS:
            for record in iter_json_records(file_path):
                text = extract_text_from_record(record)
                for idx, part in enumerate(split_text(text, max_chars=max_chars, overlap_chars=overlap_chars)):
                    payload = dict(record)
                    payload["text"] = part
                    payload.setdefault("raw_text", part)
                    payload.setdefault("embedding_text", part)
                    payload.setdefault("source_file", str(file_path))
                    payload["chunk_index"] = idx
                    chunks.append(payload)
                    if limit and len(chunks) >= limit:
                        return chunks
            continue

        raw = file_path.read_text(encoding="utf-8", errors="replace")
        for idx, part in enumerate(split_text(raw, max_chars=max_chars, overlap_chars=overlap_chars)):
            chunks.append({
                "text": part,
                "raw_text": part,
                "embedding_text": part,
                "source_file": str(file_path),
                "title": file_path.stem,
                "chunk_index": idx,
                "language": "ru",
            })
            if limit and len(chunks) >= limit:
                return chunks
    return chunks


class FastEmbedder:
    def __init__(self, *, model_name: str, cache_dir: str, threads: int, batch_size: int, dimensions: int) -> None:
        try:
            from fastembed import TextEmbedding  # type: ignore
        except ImportError as exc:
            raise RuntimeError("Install fastembed first: pip install fastembed") from exc
        self.model_name = model_name
        self.batch_size = max(1, batch_size)
        self.dimensions = dimensions
        kwargs: dict[str, Any] = {"model_name": model_name}
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            kwargs["cache_dir"] = cache_dir
        if threads > 0:
            kwargs["threads"] = threads
        try:
            self.model = TextEmbedding(**kwargs)
        except TypeError:
            kwargs.pop("threads", None)
            self.model = TextEmbedding(**kwargs)

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            generated = self.model.embed(texts, batch_size=self.batch_size)
        except TypeError:
            generated = self.model.embed(texts)
        arr = np.array([v.tolist() if hasattr(v, "tolist") else list(v) for v in generated], dtype=np.float32)
        if arr.size:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms
        vectors = arr.tolist()
        if vectors and self.dimensions > 0 and len(vectors[0]) != self.dimensions:
            raise RuntimeError(f"FastEmbed dimension mismatch: expected {self.dimensions}, got {len(vectors[0])}")
        return vectors


async def qdrant_request(method: str, path: str, *, qdrant_url: str, api_key: str, json_body: Any | None = None) -> Any:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.request(method, f"{qdrant_url.rstrip('/')}{path}", headers=headers, json=json_body)
        response.raise_for_status()
        if response.content:
            return response.json()
        return None


async def recreate_collection(*, qdrant_url: str, api_key: str, collection: str, dimensions: int, vector_name: str, recreate: bool) -> None:
    if recreate:
        try:
            await qdrant_request("DELETE", f"/collections/{collection}", qdrant_url=qdrant_url, api_key=api_key)
            print(f"Deleted existing collection: {collection}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
    if vector_name:
        body = {"vectors": {vector_name: {"size": dimensions, "distance": "Cosine"}}}
    else:
        body = {"vectors": {"size": dimensions, "distance": "Cosine"}}
    await qdrant_request("PUT", f"/collections/{collection}", qdrant_url=qdrant_url, api_key=api_key, json_body=body)
    print(f"Collection ready: {collection} ({dimensions} dims, vector_name={vector_name or 'default'})")


async def upsert_points(*, qdrant_url: str, api_key: str, collection: str, vector_name: str, payloads: list[dict[str, Any]], vectors: list[list[float]]) -> None:
    points = []
    for payload, vector in zip(payloads, vectors):
        text = str(payload.get("text") or payload.get("raw_text") or "")
        point_id = stable_id(f"{payload.get('source_file')}:{payload.get('chunk_index')}:{text[:80]}")
        point_vector: Any = {vector_name: vector} if vector_name else vector
        points.append({"id": point_id, "vector": point_vector, "payload": payload})
    body = {"points": points}
    await qdrant_request("PUT", f"/collections/{collection}/points?wait=true", qdrant_url=qdrant_url, api_key=api_key, json_body=body)


async def main_async() -> int:
    base_dir = Path(__file__).resolve().parents[1]
    load_dotenv_like(base_dir / ".env")
    load_dotenv_like(base_dir / ".env.local")
    load_dotenv_like(base_dir / ".env.render")

    parser = argparse.ArgumentParser(description="Index AI-Talapker files into Qdrant Cloud using FastEmbed CPU embeddings.")
    parser.add_argument("--source", default=str(base_dir / "data"), help="File or directory to index. Default: backend/data")
    parser.add_argument("--collection", default=env_str("QDRANT_COLLECTION", "ai_talapker_fastembed_384"))
    parser.add_argument("--recreate", action="store_true", help="Delete and recreate the Qdrant collection before indexing.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of chunks for a quick demo.")
    parser.add_argument("--batch-size", type=int, default=env_int("FASTEMBED_BATCH_SIZE", 16))
    parser.add_argument("--max-chars", type=int, default=1800)
    parser.add_argument("--overlap-chars", type=int, default=200)
    args = parser.parse_args()

    qdrant_url = env_str("QDRANT_URL")
    qdrant_key = env_str("QDRANT_API_KEY")
    collection = args.collection
    vector_name = env_str("QDRANT_VECTOR_NAME", "")
    model_name = env_str("FASTEMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    dimensions = env_int("FASTEMBED_DIMENSIONS", 384)
    cache_dir = env_str("FASTEMBED_CACHE_DIR", str(base_dir / "models" / "fastembed"))
    threads = env_int("FASTEMBED_THREADS", 1)

    missing = [name for name, value in {"QDRANT_URL": qdrant_url, "QDRANT_API_KEY": qdrant_key}.items() if not value]
    if missing:
        print("Missing required env: " + ", ".join(missing), file=sys.stderr)
        return 2

    source = Path(args.source)
    if not source.is_absolute():
        source = base_dir / source
    chunks = collect_chunks(source, max_chars=args.max_chars, overlap_chars=args.overlap_chars, limit=args.limit or None)
    if not chunks:
        print(f"No chunks found in {source}", file=sys.stderr)
        return 3

    print(f"Collected chunks: {len(chunks)} from {source}")
    embedder = FastEmbedder(
        model_name=model_name,
        cache_dir=cache_dir,
        threads=threads,
        batch_size=args.batch_size,
        dimensions=dimensions,
    )
    await recreate_collection(
        qdrant_url=qdrant_url,
        api_key=qdrant_key,
        collection=collection,
        dimensions=dimensions,
        vector_name=vector_name,
        recreate=args.recreate,
    )

    for start in range(0, len(chunks), args.batch_size):
        batch = chunks[start : start + args.batch_size]
        texts = ["passage: " + str(item.get("text") or item.get("raw_text") or "") for item in batch]
        vectors = embedder.embed(texts)
        await upsert_points(
            qdrant_url=qdrant_url,
            api_key=qdrant_key,
            collection=collection,
            vector_name=vector_name,
            payloads=batch,
            vectors=vectors,
        )
        print(f"Upserted {min(start + len(batch), len(chunks))}/{len(chunks)}")
        time.sleep(0.1)

    print("Done.")
    return 0


def main() -> int:
    import asyncio

    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
