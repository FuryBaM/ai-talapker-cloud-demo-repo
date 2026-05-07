from __future__ import annotations

import json
from pathlib import Path

from core.config import CURATED_ENTRIES_PATH
from core.schemas import CuratedEntry


def _target(path: str = CURATED_ENTRIES_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def load_curated_entries(path: str = CURATED_ENTRIES_PATH) -> list[CuratedEntry]:
    target = _target(path)
    if not target.exists():
        return []
    raw = json.loads(target.read_text(encoding="utf-8") or "[]")
    return [CuratedEntry(**item) for item in raw]


def save_curated_entries(entries: list[CuratedEntry], path: str = CURATED_ENTRIES_PATH) -> list[CuratedEntry]:
    target = _target(path)
    target.write_text(
        json.dumps([entry.model_dump(by_alias=True) for entry in entries], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return load_curated_entries(path)


def upsert_curated_entry(entry: CuratedEntry, path: str = CURATED_ENTRIES_PATH) -> list[CuratedEntry]:
    entries = [item for item in load_curated_entries(path) if item.entry_id != entry.entry_id]
    entries.append(entry)
    entries.sort(key=lambda item: item.entry_id)
    return save_curated_entries(entries, path)


def delete_curated_entry(entry_id: str, path: str = CURATED_ENTRIES_PATH) -> list[CuratedEntry]:
    entries = [item for item in load_curated_entries(path) if item.entry_id != entry_id]
    return save_curated_entries(entries, path)
