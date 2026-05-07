#!/usr/bin/env python3
"""
Bootstrap Qdrant Cloud for the AI-Talapker free-cloud demo.

This script is intentionally standalone. It does not import the project RAG code,
so it can run on a weak free service or locally without torch/transformers/GGUF.

Default flow:
  assistant/data or assistant/storage/rag_chunks.jsonl
  -> simple chunk extraction
  -> OpenRouter embeddings
  -> Qdrant Cloud collection upsert

Required env:
  OPENROUTER_API_KEY
  QDRANT_URL
  QDRANT_API_KEY

Optional env:
  OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
  OPENROUTER_EMBEDDING_DIMENSIONS=1536
  QDRANT_COLLECTION=ai_talapker_openrouter_1536
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
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-talapker:{digest}"))


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
            # Hard split for extremely long paragraphs.
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
        # Accept either a single record or nested list containers.
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
                "source_file": str(file_path),
                "title": file_path.stem,
                "chunk_index": idx,
                "lang": "ru",
            })
            if limit and len(chunks) >= limit:
                return chunks
    return chunks


async def openrouter_embeddings(texts: list[str], *, api_key: str, model: str, dimensions: int, base_url: str) -> list[list[float]]:
    payload: dict[str, Any] = {"model": model, "input": texts}
    if dimensions > 0:
        payload["dimensions"] = dimensions
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://n8n.kstu.kz",
        "X-Title": "AI-Talapker Demo Indexer",
    }
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(f"{base_url.rstrip('/')}/embeddings", headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    return [item["embedding"] for item in data["data"]]


async def qdrant_request(method: str, path: str, *, qdrant_url: str, api_key: str, json_body: Any | None = None) -> Any:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["api-key"] = api_key
    async with httpx.AsyncClient(timeout=90) as client:
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
        text = payload.get("text", "")
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

    parser = argparse.ArgumentParser(description="Index AI-Talapker files into Qdrant Cloud using OpenRouter embeddings.")
    parser.add_argument("--source", default=str(base_dir / "data"), help="File or directory to index. Default: assistant/data")
    parser.add_argument("--collection", default=env_str("QDRANT_COLLECTION", "ai_talapker_openrouter_1536"))
    parser.add_argument("--recreate", action="store_true", help="Delete and recreate the Qdrant collection before indexing.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of chunks for a quick demo.")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--max-chars", type=int, default=1800)
    parser.add_argument("--overlap-chars", type=int, default=200)
    args = parser.parse_args()

    openrouter_key = env_str("OPENROUTER_API_KEY")
    qdrant_url = env_str("QDRANT_URL")
    qdrant_key = env_str("QDRANT_API_KEY")
    embedding_model = env_str("OPENROUTER_EMBEDDING_MODEL", "openai/text-embedding-3-small")
    embedding_dimensions = env_int("OPENROUTER_EMBEDDING_DIMENSIONS", 1536)
    openrouter_base_url = env_str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    vector_name = env_str("QDRANT_VECTOR_NAME", "")

    missing = [name for name, value in {
        "OPENROUTER_API_KEY": openrouter_key,
        "QDRANT_URL": qdrant_url,
        "QDRANT_API_KEY": qdrant_key,
    }.items() if not value]
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
    await recreate_collection(
        qdrant_url=qdrant_url,
        api_key=qdrant_key,
        collection=args.collection,
        dimensions=embedding_dimensions,
        vector_name=vector_name,
        recreate=args.recreate,
    )

    for start in range(0, len(chunks), args.batch_size):
        batch = chunks[start : start + args.batch_size]
        texts = [str(item.get("text") or "") for item in batch]
        vectors = await openrouter_embeddings(
            texts,
            api_key=openrouter_key,
            model=embedding_model,
            dimensions=embedding_dimensions,
            base_url=openrouter_base_url,
        )
        await upsert_points(
            qdrant_url=qdrant_url,
            api_key=qdrant_key,
            collection=args.collection,
            vector_name=vector_name,
            payloads=batch,
            vectors=vectors,
        )
        print(f"Upserted {min(start + len(batch), len(chunks))}/{len(chunks)}")
        time.sleep(0.2)

    print("Done.")
    return 0


def main() -> int:
    import asyncio

    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
