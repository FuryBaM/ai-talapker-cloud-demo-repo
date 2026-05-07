from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.config import INPUT_DATA_DIR, KNOWLEDGE_ENTRIES_PATH, KNOWLEDGE_REGISTRY_PATH
from core.entry_store import load_curated_entries
from core.knowledge_handlers import build_entries_for_source
from core.knowledge_registry import load_registry


@dataclass
class IngestStats:
    input_files: int = 0
    output_files: int = 0
    skipped_files: int = 0
    registry_sources: int = 0
    entries_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "input_files": self.input_files,
            "output_files": self.output_files,
            "skipped_files": self.skipped_files,
            "registry_sources": self.registry_sources,
            "entries_count": self.entries_count,
        }


def _write_entries(entries_path: str, entries: list[dict]) -> None:
    target = Path(entries_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def rebuild_processed_data(
    input_dir: str = INPUT_DATA_DIR,
    registry_path: str = KNOWLEDGE_REGISTRY_PATH,
    entries_path: str = KNOWLEDGE_ENTRIES_PATH,
) -> IngestStats:
    source_root = Path(input_dir)
    if not source_root.exists():
        return IngestStats()

    sources = load_registry(registry_path=registry_path, input_dir=input_dir)
    raw_files = [path for path in source_root.rglob("*") if path.is_file()]

    entries = []
    output_files = 0
    for source in sources:
        built = build_entries_for_source(source, input_root=input_dir)
        if built:
            output_files += 1
            entries.extend(entry.model_dump(by_alias=True) for entry in built)

    for entry in load_curated_entries():
        if entry.enabled:
            entries.append(entry.model_dump(by_alias=True))

    _write_entries(entries_path, entries)
    input_files = len(raw_files)
    skipped_files = max(0, len([source for source in sources if source.enabled]) - output_files)
    return IngestStats(
        input_files=input_files,
        output_files=output_files,
        skipped_files=skipped_files,
        registry_sources=len(sources),
        entries_count=len(entries),
    )
