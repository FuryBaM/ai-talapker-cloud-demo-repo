from __future__ import annotations

import gc
import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

import numpy as np
from blingfire import text_to_sentences
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

from core.config import (
    EMBED_BATCH_SIZE,
    KNOWLEDGE_ENTRIES_PATH,
    MAX_CTX_CHUNKS,
    OVERLAP_TOKENS,
    QDRANT_API_KEY,
    QDRANT_COLLECTION,
    QDRANT_PATH,
    QDRANT_PREFER_GRPC,
    QDRANT_TIMEOUT,
    QDRANT_URL,
    QDRANT_VECTOR_NAME,
    RAG_CHUNKS_PATH,
    RAG_DOCUMENTS_PATH,
    SIM_THRESHOLD,
    TARGET_TOKENS,
    TOKEN_LIMIT,
)
from core.data_ingest import IngestStats, rebuild_processed_data
from core.model_store import embed_model, embed_tokenizer
from core.schemas import Chunk, KnowledgeEntry, RagChunk, RagDocument


@dataclass
class QdrantIndex:
    client: QdrantClient
    collection_name: str


def close_index(index: "QdrantIndex | None") -> None:
    if not index:
        return
    try:
        index.client.close()
    except Exception:
        pass


def _use_remote_qdrant() -> bool:
    return bool(str(QDRANT_URL or "").strip())


def _make_qdrant_client(qdrant_path: str = QDRANT_PATH) -> QdrantClient:
    if _use_remote_qdrant():
        return QdrantClient(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY or None,
            prefer_grpc=QDRANT_PREFER_GRPC,
            timeout=QDRANT_TIMEOUT,
        )
    return QdrantClient(path=qdrant_path)


def _vectors_config(vector_size: int):
    params = VectorParams(size=int(vector_size), distance=Distance.COSINE)
    if QDRANT_VECTOR_NAME:
        return {QDRANT_VECTOR_NAME: params}
    return params


def _point_vector(embedding: np.ndarray | list[float]):
    vector = embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)
    if QDRANT_VECTOR_NAME:
        return {QDRANT_VECTOR_NAME: vector}
    return vector


def _query_vector(embedding: np.ndarray | list[float]):
    vector = embedding.tolist() if hasattr(embedding, "tolist") else list(embedding)
    if QDRANT_VECTOR_NAME:
        return (QDRANT_VECTOR_NAME, vector)
    return vector


def _jsonl_read(path: str | Path) -> list[dict]:
    source = Path(path)
    if not source.exists():
        return []
    records: list[dict] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _jsonl_write(path: str | Path, records: list[dict]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _upsert_jsonl_records(path: str | Path, records: list[dict], key: str) -> list[dict]:
    existing = _jsonl_read(path)
    replacement_keys = {record.get(key) for record in records}
    merged = [row for row in existing if row.get(key) not in replacement_keys]
    merged.extend(records)
    _jsonl_write(path, merged)
    return merged


def _remove_jsonl_records(path: str | Path, key: str, values: list[str]) -> list[dict]:
    existing = _jsonl_read(path)
    value_set = set(values)
    kept = [row for row in existing if row.get(key) not in value_set]
    _jsonl_write(path, kept)
    return kept


def _slugify(value: str) -> str:
    chars = []
    for char in str(value or "").lower():
        chars.append(char if char.isalnum() else "_")
    return "".join(chars).strip("_") or "entry"


def _point_id(value: str) -> str:
    stable_value = str(value or "chunk").strip() or "chunk"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ai-talapker:qdrant:{stable_value}"))


def _payload_to_context(payload: dict) -> str:
    return str(payload.get("raw_text") or payload.get("text") or "").strip()


def split_sentences(text: str) -> List[str]:
    sents = text_to_sentences(text).split("\n")
    return [s.strip() for s in sents if s.strip()]


def token_len(text: str, tokenizer=None, token_limit: int = TOKEN_LIMIT) -> int:
    tokenizer = tokenizer or embed_tokenizer
    return len(
        tokenizer.encode(
            text,
            add_special_tokens=False,
            truncation=True,
            max_length=token_limit,
        )
    )


def truncate_for_embedding(text: str, tokenizer=None, limit: int = TOKEN_LIMIT) -> str:
    tokenizer = tokenizer or embed_tokenizer
    token_ids = tokenizer.encode(
        text,
        add_special_tokens=False,
        truncation=True,
        max_length=limit,
    )
    return tokenizer.decode(token_ids, skip_special_tokens=True).strip()


def pack_by_tokens(
    sentences: List[str],
    tokenizer=None,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    token_limit: int = TOKEN_LIMIT,
):
    tokenizer = tokenizer or embed_tokenizer
    cur, cur_tok = [], 0
    for sentence in sentences:
        token_count = token_len(sentence, tokenizer=tokenizer, token_limit=token_limit)
        if token_count > token_limit:
            sentence = truncate_for_embedding(sentence, tokenizer=tokenizer, limit=token_limit)
            token_count = token_len(sentence, tokenizer=tokenizer, token_limit=token_limit)
        if cur_tok + token_count <= target_tokens:
            cur.append(sentence)
            cur_tok += token_count
            continue
        if cur:
            yield " ".join(cur)
        cur = cur[-1:] if overlap_tokens and cur else []
        cur_tok = sum(token_len(item, tokenizer=tokenizer, token_limit=token_limit) for item in cur)
        cur.append(sentence)
        cur_tok += token_count
    if cur:
        yield " ".join(cur)


def _embedding_size(model=None) -> int:
    model = model or embed_model
    return int(model.get_sentence_embedding_dimension())


def _clear_embedding_cache() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _encode_texts(model, texts: list[str], batch_size: int = EMBED_BATCH_SIZE) -> np.ndarray:
    if not texts:
        return np.array([], dtype=np.float32)
    vectors = []
    safe_batch_size = max(1, int(batch_size or 1))
    for start in range(0, len(texts), safe_batch_size):
        batch = texts[start : start + safe_batch_size]
        encoded = model.encode(
            batch,
            convert_to_numpy=True,
            normalize_embeddings=True,
            batch_size=safe_batch_size,
            show_progress_bar=False,
        ).astype(np.float32)
        vectors.append(encoded)
        _clear_embedding_cache()
    return np.vstack(vectors).astype(np.float32)


def make_query(text: str, model=None, tokenizer=None, token_limit: int = TOKEN_LIMIT) -> np.ndarray:
    model = model or embed_model
    tokenizer = tokenizer or embed_tokenizer
    text = truncate_for_embedding(text, tokenizer=tokenizer, limit=token_limit)
    return _encode_texts(model, [f"query: {text}"], batch_size=1)[0]


def _read_entries(entries_path: str = KNOWLEDGE_ENTRIES_PATH) -> list[KnowledgeEntry]:
    return [KnowledgeEntry(**item) for item in _jsonl_read(entries_path)]


def upsert_entries(entries: list[KnowledgeEntry], entries_path: str = KNOWLEDGE_ENTRIES_PATH) -> list[dict]:
    return _upsert_jsonl_records(entries_path, [entry.model_dump(by_alias=True) for entry in entries], "entry_id")


def remove_entries_by_ids(entry_ids: list[str], entries_path: str = KNOWLEDGE_ENTRIES_PATH) -> list[dict]:
    return _remove_jsonl_records(entries_path, "entry_id", entry_ids)


def remove_entries_by_source_id(source_id: str, entries_path: str = KNOWLEDGE_ENTRIES_PATH) -> list[dict]:
    existing = _jsonl_read(entries_path)
    kept = [row for row in existing if row.get("source_id") != source_id]
    _jsonl_write(entries_path, kept)
    return kept


def build_rag_documents(entries_path: str = KNOWLEDGE_ENTRIES_PATH, output_path: str = RAG_DOCUMENTS_PATH) -> list[RagDocument]:
    documents: list[RagDocument] = []
    for entry in _read_entries(entries_path):
        documents.append(
            RagDocument(
                doc_id=entry.entry_id,
                domain=entry.domain or entry.class_name,
                title=entry.title,
                text=entry.text,
                embedding_text=entry.embedding_text,
                source_file=entry.source_file,
                source_url=entry.source_url,
                metadata={
                    **entry.metadata,
                    "source_id": entry.source_id,
                    "schema": entry.schema_name,
                    "domain": entry.domain or entry.class_name,
                    "class_name": entry.class_name,
                    "education_level": entry.education_level,
                    "language": entry.language,
                },
            )
        )
    _jsonl_write(output_path, [document.model_dump() for document in documents])
    return documents


def build_rag_documents_for_entries(entries: list[KnowledgeEntry]) -> list[RagDocument]:
    documents: list[RagDocument] = []
    for entry in entries:
        documents.append(
            RagDocument(
                doc_id=entry.entry_id,
                domain=entry.domain or entry.class_name,
                title=entry.title,
                text=entry.text,
                embedding_text=entry.embedding_text,
                source_file=entry.source_file,
                source_url=entry.source_url,
                metadata={
                    **entry.metadata,
                    "source_id": entry.source_id,
                    "schema": entry.schema_name,
                    "domain": entry.domain or entry.class_name,
                    "class_name": entry.class_name,
                    "education_level": entry.education_level,
                    "language": entry.language,
                },
            )
        )
    return documents


def upsert_rag_documents(documents: list[RagDocument], documents_path: str = RAG_DOCUMENTS_PATH) -> list[dict]:
    return _upsert_jsonl_records(documents_path, [document.model_dump() for document in documents], "doc_id")


def remove_rag_documents_by_doc_ids(doc_ids: list[str], documents_path: str = RAG_DOCUMENTS_PATH) -> list[dict]:
    return _remove_jsonl_records(documents_path, "doc_id", doc_ids)


def remove_rag_documents_by_source_id(source_id: str, documents_path: str = RAG_DOCUMENTS_PATH) -> list[dict]:
    existing = _jsonl_read(documents_path)
    kept = [row for row in existing if (row.get("metadata", {}) or {}).get("source_id") != source_id]
    _jsonl_write(documents_path, kept)
    return kept


def _document_chunks(
    document: RagDocument,
    tokenizer=None,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    token_limit: int = TOKEN_LIMIT,
) -> list[RagChunk]:
    tokenizer = tokenizer or embed_tokenizer
    base_text = document.text.strip()
    base_embedding_text = (document.embedding_text or document.text).strip()
    logical_group_id = str((document.metadata or {}).get("logical_group_id") or document.doc_id)
    base_metadata = {**document.metadata, "logical_group_id": logical_group_id}
    if token_len(base_embedding_text, tokenizer=tokenizer, token_limit=token_limit) <= target_tokens:
        chunk_id = f"{document.doc_id}_chunk_1"
        return [
            RagChunk(
                chunk_id=chunk_id,
                doc_id=document.doc_id,
                domain=document.domain,
                title=document.title,
                text=base_text,
                embedding_text=truncate_for_embedding(base_embedding_text, tokenizer=tokenizer, limit=token_limit),
                source_file=document.source_file,
                source_url=document.source_url,
                metadata={**base_metadata, "chunk_index": 1, "chunk_count": 1, "sibling_chunk_ids": [chunk_id]},
            )
        ]

    pieces = [piece for piece in pack_by_tokens(split_sentences(base_embedding_text) or [base_embedding_text], tokenizer=tokenizer, target_tokens=target_tokens, overlap_tokens=overlap_tokens, token_limit=token_limit) if piece.strip()]
    chunk_ids = [f"{document.doc_id}_chunk_{index}" for index in range(1, len(pieces) + 1)]
    return [
        RagChunk(
            chunk_id=chunk_id,
            doc_id=document.doc_id,
            domain=document.domain,
            title=document.title,
            text=base_text,
            embedding_text=truncate_for_embedding(piece, tokenizer=tokenizer, limit=token_limit),
            source_file=document.source_file,
            source_url=document.source_url,
            metadata={**base_metadata, "chunk_index": index, "chunk_count": len(chunk_ids), "sibling_chunk_ids": chunk_ids},
        )
        for index, (chunk_id, piece) in enumerate(zip(chunk_ids, pieces), start=1)
    ]


def build_rag_chunks(
    documents_path: str = RAG_DOCUMENTS_PATH,
    output_path: str = RAG_CHUNKS_PATH,
    tokenizer=None,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    token_limit: int = TOKEN_LIMIT,
) -> list[RagChunk]:
    chunks: list[RagChunk] = []
    for raw_document in _jsonl_read(documents_path):
        document = RagDocument(**raw_document)
        chunks.extend(_document_chunks(document, tokenizer=tokenizer, target_tokens=target_tokens, overlap_tokens=overlap_tokens, token_limit=token_limit))
    _jsonl_write(output_path, [chunk.model_dump() for chunk in chunks])
    return chunks


def build_rag_chunks_for_documents(
    documents: list[RagDocument],
    tokenizer=None,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    token_limit: int = TOKEN_LIMIT,
) -> list[RagChunk]:
    chunks: list[RagChunk] = []
    for document in documents:
        chunks.extend(
            _document_chunks(
                document,
                tokenizer=tokenizer,
                target_tokens=target_tokens,
                overlap_tokens=overlap_tokens,
                token_limit=token_limit,
            )
        )
    return chunks


def upsert_rag_chunks(chunks: list[RagChunk], chunks_path: str = RAG_CHUNKS_PATH) -> list[dict]:
    return _upsert_jsonl_records(chunks_path, [chunk.model_dump() for chunk in chunks], "chunk_id")


def remove_rag_chunks_by_doc_ids(doc_ids: list[str], chunks_path: str = RAG_CHUNKS_PATH) -> list[dict]:
    existing = _jsonl_read(chunks_path)
    doc_id_set = set(doc_ids)
    kept = [row for row in existing if row.get("doc_id") not in doc_id_set]
    _jsonl_write(chunks_path, kept)
    return kept


def remove_rag_chunks_by_source_id(source_id: str, chunks_path: str = RAG_CHUNKS_PATH) -> list[dict]:
    existing = _jsonl_read(chunks_path)
    kept = [row for row in existing if (row.get("metadata", {}) or {}).get("source_id") != source_id]
    _jsonl_write(chunks_path, kept)
    return kept


def _qdrant_meta_path(qdrant_path: str | Path) -> Path:
    base = Path(qdrant_path)
    return base.parent / f"{base.name}_meta.json"


def _read_qdrant_meta(qdrant_path: str | Path) -> dict:
    path = _qdrant_meta_path(qdrant_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_qdrant_meta(qdrant_path: str | Path, *, vector_size: int) -> None:
    path = _qdrant_meta_path(qdrant_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"vector_size": int(vector_size)}, ensure_ascii=False, indent=2), encoding="utf-8")


def _reset_qdrant_storage(qdrant_path: str | Path) -> None:
    target = Path(qdrant_path)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)


def _should_reset_for_dimension_change(qdrant_path: str | Path, *, vector_size: int) -> bool:
    meta = _read_qdrant_meta(qdrant_path)
    previous_size = meta.get("vector_size")
    try:
        return previous_size is not None and int(previous_size) != int(vector_size)
    except Exception:
        return False




def _collection_point_count(client: QdrantClient, collection_name: str) -> int | None:
    try:
        result = client.count(collection_name=collection_name, exact=False)
        return int(getattr(result, "count", 0))
    except Exception:
        try:
            info = client.get_collection(collection_name=collection_name)
            value = getattr(info, "points_count", None)
            return int(value) if value is not None else None
        except Exception:
            return None


def _can_reuse_existing_collection(client: QdrantClient, collection_name: str, chunks_path: str | Path) -> bool:
    count = _collection_point_count(client, collection_name)
    if count is None:
        return True
    if count > 0:
        return True
    # Empty collection after a failed/partial indexing run should be rebuilt when chunks exist.
    try:
        raw_chunks = _jsonl_read(chunks_path)
        return not any((chunk.get("embedding_text") or "").strip() for chunk in raw_chunks)
    except Exception:
        return True


def build_index(
    rebuild_data: bool = False,
    force_reindex: bool = False,
    model=None,
    tokenizer=None,
    entries_path: str = KNOWLEDGE_ENTRIES_PATH,
    documents_path: str = RAG_DOCUMENTS_PATH,
    chunks_path: str = RAG_CHUNKS_PATH,
    qdrant_path: str = QDRANT_PATH,
    collection_name: str = QDRANT_COLLECTION,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    token_limit: int = TOKEN_LIMIT,
    batch_size: int = EMBED_BATCH_SIZE,
    _retry_on_dimension_error: bool = True,
) -> QdrantIndex:
    model = model or embed_model
    tokenizer = tokenizer or embed_tokenizer
    vector_size = _embedding_size(model=model)

    if rebuild_data or not Path(entries_path).exists():
        rebuild_processed_data()
    if rebuild_data or not Path(documents_path).exists():
        build_rag_documents(entries_path=entries_path, output_path=documents_path)
    if rebuild_data or not Path(chunks_path).exists():
        build_rag_chunks(
            documents_path=documents_path,
            output_path=chunks_path,
            tokenizer=tokenizer,
            target_tokens=target_tokens,
            overlap_tokens=overlap_tokens,
            token_limit=token_limit,
        )

    if not _use_remote_qdrant() and _should_reset_for_dimension_change(qdrant_path, vector_size=vector_size):
        _reset_qdrant_storage(qdrant_path)

    client = _make_qdrant_client(qdrant_path)
    if client.collection_exists(collection_name):
        if not rebuild_data and not force_reindex and _can_reuse_existing_collection(client, collection_name, chunks_path):
            return QdrantIndex(client=client, collection_name=collection_name)
        client.delete_collection(collection_name)
    client.create_collection(
        collection_name=collection_name,
        vectors_config=_vectors_config(vector_size),
    )

    raw_chunks = _jsonl_read(chunks_path)
    valid_chunks = [chunk for chunk in raw_chunks if chunk.get("embedding_text")]
    safe_batch_size = max(1, int(batch_size or 1))
    try:
        for start in range(0, len(valid_chunks), safe_batch_size):
            chunk_batch = valid_chunks[start : start + safe_batch_size]
            texts = [f"passage: {chunk['embedding_text']}" for chunk in chunk_batch]
            embeddings = _encode_texts(model, texts, batch_size=safe_batch_size)
            points = []
            for offset, (chunk, embedding) in enumerate(zip(chunk_batch, embeddings)):
                metadata = dict(chunk.get("metadata", {}) or {})
                chunk_id = chunk.get("chunk_id") or f"chunk_{start + offset}"
                points.append(
                    PointStruct(
                        id=_point_id(chunk_id),
                        vector=_point_vector(embedding),
                        payload={
                            "embedding_text": chunk["embedding_text"],
                            "text": chunk["embedding_text"],
                            "raw_text": chunk["text"],
                            "title": chunk.get("title", ""),
                            "source_file": chunk.get("source_file", ""),
                            "source_url": chunk.get("source_url"),
                            "doc_id": chunk.get("doc_id", ""),
                            "chunk_id": chunk_id,
                            "source_id": metadata.get("source_id", ""),
                            "schema": metadata.get("schema", ""),
                            "domain": metadata.get("domain", metadata.get("class_name", chunk.get("domain", "general"))),
                            "class_name": metadata.get("class_name", chunk.get("domain", "general")),
                            "education_level": metadata.get("education_level"),
                            "language": metadata.get("language"),
                            "entry_type": metadata.get("entry_type") or metadata.get("schema", ""),
                            "logical_group_id": metadata.get("logical_group_id"),
                            "chunk_index": metadata.get("chunk_index"),
                            "chunk_count": metadata.get("chunk_count"),
                            "sibling_chunk_ids": metadata.get("sibling_chunk_ids", []),
                            "expansion_policy": metadata.get("expansion_policy"),
                            "list_items": metadata.get("list_items", []),
                            "metadata": metadata,
                        },
                    )
                )
            if points:
                client.upsert(collection_name=collection_name, points=points)
            _clear_embedding_cache()
    except ValueError as exc:
        message = str(exc).lower()
        if _retry_on_dimension_error and "shape" in message and "could not broadcast" in message:
            close_index(QdrantIndex(client=client, collection_name=collection_name))
            if not _use_remote_qdrant():
                _reset_qdrant_storage(qdrant_path)
            return build_index(
                rebuild_data=False,
                force_reindex=True,
                model=model,
                tokenizer=tokenizer,
                entries_path=entries_path,
                documents_path=documents_path,
                chunks_path=chunks_path,
                qdrant_path=qdrant_path,
                collection_name=collection_name,
                target_tokens=target_tokens,
                overlap_tokens=overlap_tokens,
                token_limit=token_limit,
                batch_size=batch_size,
                _retry_on_dimension_error=False,
            )
        raise

    if not _use_remote_qdrant():
        _write_qdrant_meta(qdrant_path, vector_size=vector_size)
    return QdrantIndex(client=client, collection_name=collection_name)


def _chunk_point(chunk: RagChunk, embedding: np.ndarray) -> PointStruct:
    metadata = dict(chunk.metadata or {})
    return PointStruct(
        id=_point_id(chunk.chunk_id),
        vector=_point_vector(embedding),
        payload={
            "embedding_text": chunk.embedding_text,
            "text": chunk.embedding_text,
            "raw_text": chunk.text,
            "title": chunk.title,
            "source_file": chunk.source_file,
            "source_url": chunk.source_url,
            "doc_id": chunk.doc_id,
            "chunk_id": chunk.chunk_id,
            "source_id": metadata.get("source_id", ""),
            "schema": metadata.get("schema", ""),
            "domain": metadata.get("domain", metadata.get("class_name", chunk.domain)),
            "class_name": metadata.get("class_name", chunk.domain),
            "education_level": metadata.get("education_level"),
            "language": metadata.get("language"),
            "entry_type": metadata.get("entry_type") or metadata.get("schema", ""),
            "logical_group_id": metadata.get("logical_group_id"),
            "chunk_index": metadata.get("chunk_index"),
            "chunk_count": metadata.get("chunk_count"),
            "sibling_chunk_ids": metadata.get("sibling_chunk_ids", []),
            "expansion_policy": metadata.get("expansion_policy"),
            "list_items": metadata.get("list_items", []),
            "metadata": metadata,
        },
    )


def upsert_chunks_to_index(
    index: QdrantIndex,
    chunks: list[RagChunk],
    model=None,
    batch_size: int = EMBED_BATCH_SIZE,
) -> int:
    model = model or embed_model
    valid_chunks = [chunk for chunk in chunks if (chunk.embedding_text or "").strip()]
    safe_batch_size = max(1, int(batch_size or 1))
    for start in range(0, len(valid_chunks), safe_batch_size):
        chunk_batch = valid_chunks[start : start + safe_batch_size]
        texts = [f"passage: {chunk.embedding_text}" for chunk in chunk_batch]
        embeddings = _encode_texts(model, texts, batch_size=safe_batch_size)
        points = [_chunk_point(chunk, embedding) for chunk, embedding in zip(chunk_batch, embeddings)]
        if points:
            index.client.upsert(collection_name=index.collection_name, points=points)
        _clear_embedding_cache()
    return len(valid_chunks)


def delete_chunks_from_index_by_filter(index: QdrantIndex, *, source_id: str | None = None, doc_ids: list[str] | None = None) -> None:
    if source_id:
        index.client.delete(
            collection_name=index.collection_name,
            points_selector=Filter(
                must=[FieldCondition(key="source_id", match=MatchValue(value=source_id))]
            ),
        )
    elif doc_ids:
        index.client.delete(
            collection_name=index.collection_name,
            points_selector=Filter(
                should=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id)) for doc_id in doc_ids]
            ),
        )


def rebuild_index(
    model=None,
    tokenizer=None,
    entries_path: str = KNOWLEDGE_ENTRIES_PATH,
    documents_path: str = RAG_DOCUMENTS_PATH,
    chunks_path: str = RAG_CHUNKS_PATH,
    qdrant_path: str = QDRANT_PATH,
    collection_name: str = QDRANT_COLLECTION,
    target_tokens: int = TARGET_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
    token_limit: int = TOKEN_LIMIT,
    batch_size: int = EMBED_BATCH_SIZE,
) -> tuple[QdrantIndex, IngestStats]:
    stats = rebuild_processed_data(entries_path=entries_path)
    build_rag_documents(entries_path=entries_path, output_path=documents_path)
    build_rag_chunks(documents_path=documents_path, output_path=chunks_path, tokenizer=tokenizer, target_tokens=target_tokens, overlap_tokens=overlap_tokens, token_limit=token_limit)
    return (
        build_index(
            rebuild_data=False,
            force_reindex=True,
            model=model,
            tokenizer=tokenizer,
            entries_path=entries_path,
            documents_path=documents_path,
            chunks_path=chunks_path,
            qdrant_path=qdrant_path,
            collection_name=collection_name,
            target_tokens=target_tokens,
            overlap_tokens=overlap_tokens,
            token_limit=token_limit,
            batch_size=batch_size,
        ),
        stats,
    )


def _build_filter(domains: list[str] | None = None, schemas: list[str] | None = None, education_level: str | None = None, language: str | None = None) -> Filter | None:
    must = []
    if domains:
        must.append(
            Filter(
                should=[
                    condition
                    for domain in domains
                    if domain
                    for condition in (
                        FieldCondition(key="domain", match=MatchValue(value=domain)),
                        FieldCondition(key="class_name", match=MatchValue(value=domain)),
                    )
                ]
            )
        )
    if schemas:
        must.append(
            Filter(
                should=[FieldCondition(key="schema", match=MatchValue(value=schema)) for schema in schemas if schema]
            )
        )
    if education_level:
        must.append(FieldCondition(key="education_level", match=MatchValue(value=education_level)))
    if language:
        must.append(FieldCondition(key="language", match=MatchValue(value=language)))
    if not must:
        return None
    return Filter(must=must)


def find_passage(
    query: str,
    index: QdrantIndex,
    top_k: int = MAX_CTX_CHUNKS,
    threshold: float = SIM_THRESHOLD,
    domains: list[str] | None = None,
    schemas: list[str] | None = None,
    education_level: str | None = None,
    language: str | None = None,
    model=None,
    tokenizer=None,
    token_limit: int = TOKEN_LIMIT,
):
    query_embedding = make_query(query, model=model, tokenizer=tokenizer, token_limit=token_limit)
    query_filter = _build_filter(domains=domains, schemas=schemas, education_level=education_level, language=language)
    results = index.client.search(
        collection_name=index.collection_name,
        query_vector=_query_vector(query_embedding),
        limit=top_k,
        with_payload=True,
        query_filter=query_filter,
    )
    filtered = [result for result in results if result.score >= threshold]
    if not filtered:
        return None
    texts = [_payload_to_context(result.payload) for result in filtered if result.payload]
    return texts[0] if len(texts) == 1 else texts


def search_debug(
    query: str,
    index: QdrantIndex,
    top_k: int = MAX_CTX_CHUNKS,
    domains: list[str] | None = None,
    schemas: list[str] | None = None,
    education_level: str | None = None,
    language: str | None = None,
):
    query_embedding = make_query(query)
    query_filter = _build_filter(domains=domains, schemas=schemas, education_level=education_level, language=language)
    results = index.client.search(
        collection_name=index.collection_name,
        query_vector=_query_vector(query_embedding),
        limit=top_k,
        with_payload=True,
        query_filter=query_filter,
    )
    hits = []
    for result in results:
        payload = result.payload or {}
        hits.append(
            {
                "score": float(result.score),
                "source_id": str(payload.get("source_id", "")),
                "class_name": str(payload.get("class_name", "")),
                "domain": str(payload.get("domain") or payload.get("class_name", "")),
                "schema": str(payload.get("schema", "")),
                "title": str(payload.get("title", "")),
                "text": _payload_to_context(payload),
                "chunk_id": str(payload.get("chunk_id", "")),
                "logical_group_id": str(payload.get("logical_group_id") or ""),
                "entry_type": str(payload.get("entry_type") or payload.get("schema") or ""),
                "metadata": dict(payload.get("metadata", {}) or {}),
            }
        )
    return hits
