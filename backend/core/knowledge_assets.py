from __future__ import annotations

import os
from pathlib import Path

from core.data_ingest import rebuild_processed_data
from core.entry_store import load_curated_entries, upsert_curated_entry
from core.knowledge_handlers import build_entries_for_source
from core.knowledge_registry import load_registry
from core.programs import load_program_catalog
from core.rag import (
    build_index,
    build_rag_chunks,
    build_rag_chunks_for_documents,
    build_rag_documents,
    build_rag_documents_for_entries,
    close_index,
    delete_chunks_from_index_by_filter,
    remove_entries_by_source_id,
    remove_entries_by_ids,
    remove_rag_chunks_by_source_id,
    remove_rag_chunks_by_doc_ids,
    remove_rag_documents_by_source_id,
    remove_rag_documents_by_doc_ids,
    upsert_chunks_to_index,
    upsert_entries,
    upsert_rag_chunks,
    upsert_rag_documents,
)


INDEX = None if os.getenv("APP_DISABLE_MODEL_LOAD", "0").strip().lower() in {"1", "true", "yes", "on"} else build_index()
PROGRAMS = load_program_catalog()


def get_index():
    return INDEX


def get_programs():
    return PROGRAMS


def reload_knowledge_assets(
    rebuild_data: bool = False,
    normalize: bool = False,
    documents: bool = False,
    chunks: bool = False,
    index: bool = False,
) -> dict[str, int | bool]:
    global INDEX, PROGRAMS

    requested_full = rebuild_data or not any([normalize, documents, chunks, index])
    normalize = normalize or requested_full
    documents = documents or requested_full
    chunks = chunks or requested_full
    index = index or requested_full

    stats = None
    documents_count = 0
    chunks_count = 0

    if normalize:
        stats = rebuild_processed_data()

    if documents:
        if stats is None and not normalize:
            stats = rebuild_processed_data()
        documents_count = len(build_rag_documents())

    if chunks:
        if documents and documents_count == 0:
            documents_count = len(build_rag_documents())
        chunks_count = len(build_rag_chunks())

    if index or requested_full:
        close_index(INDEX)
        INDEX = build_index(rebuild_data=False, force_reindex=True)

    PROGRAMS = load_program_catalog()
    return {
        "input_files": getattr(stats, "input_files", 0),
        "output_files": getattr(stats, "output_files", 0),
        "skipped_files": getattr(stats, "skipped_files", 0),
        "registry_sources": getattr(stats, "registry_sources", 0),
        "entries_count": getattr(stats, "entries_count", 0),
        "documents_count": documents_count,
        "chunks_count": chunks_count,
        "programs": len(PROGRAMS),
        "normalized": normalize,
        "documents_built": documents,
        "chunks_built": chunks,
        "index_built": index or requested_full,
    }


def reindex_source(source_id: str, input_root: str) -> dict[str, int | bool]:
    global PROGRAMS

    source = next((item for item in load_registry(input_dir=input_root) if item.source_id == source_id), None)
    if not source:
        raise ValueError(f"source not found: {source_id}")

    built_entries = build_entries_for_source(source, input_root=input_root)
    curated_entries = [entry for entry in load_curated_entries() if entry.source_id == source_id and entry.enabled]
    replacement_entries = [*built_entries, *curated_entries]
    if replacement_entries:
        remove_entries_by_source_id(source_id)
        remove_rag_documents_by_source_id(source_id)
        remove_rag_chunks_by_source_id(source_id)
        delete_chunks_from_index_by_filter(INDEX, source_id=source_id)
        upsert_entries(replacement_entries)
        documents = build_rag_documents_for_entries(replacement_entries)
        upsert_rag_documents(documents)
        chunks = build_rag_chunks_for_documents(documents)
        upsert_rag_chunks(chunks)
        upsert_chunks_to_index(INDEX, chunks)
        chunks_count = len(chunks)
    else:
        delete_chunks_from_index_by_filter(INDEX, source_id=source_id)
        remove_entries_by_source_id(source_id)
        remove_rag_documents_by_source_id(source_id)
        remove_rag_chunks_by_source_id(source_id)
        chunks_count = 0

    PROGRAMS = load_program_catalog()
    return {
        "ok": True,
        "source_id": source_id,
        "entries_count": len(replacement_entries),
        "documents_count": len(replacement_entries),
        "chunks_count": chunks_count,
    }


def reindex_curated_entry(entry) -> dict[str, int | bool]:
    global PROGRAMS

    upsert_curated_entry(entry)
    entry_id = entry.entry_id
    remove_entries_by_ids([entry_id])
    remove_rag_documents_by_doc_ids([entry_id])
    remove_rag_chunks_by_doc_ids([entry_id])
    delete_chunks_from_index_by_filter(INDEX, doc_ids=[entry_id])
    upsert_entries([entry])
    documents = build_rag_documents_for_entries([entry])
    upsert_rag_documents(documents)
    chunks = build_rag_chunks_for_documents(documents)
    upsert_rag_chunks(chunks)
    upsert_chunks_to_index(INDEX, chunks)
    PROGRAMS = load_program_catalog()
    return {
        "ok": True,
        "entry_id": entry_id,
        "entries_count": 1,
        "documents_count": len(documents),
        "chunks_count": len(chunks),
    }


def shutdown_knowledge_assets() -> None:
    close_index(INDEX)
