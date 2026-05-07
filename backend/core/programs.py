from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, List

from core.config import KNOWLEDGE_ENTRIES_PATH


@dataclass
class Program:
    name: str
    code: str | None = None
    group_name: str | None = None
    ent_subjects: List[str] = field(default_factory=list)
    min_score: int | None = None
    tags: List[str] = field(default_factory=list)
    description: str = ""
    career_paths: List[str] = field(default_factory=list)


def _jsonl_read(path: str | Path) -> list[dict]:
    source = Path(path)
    if not source.exists():
        return []
    records = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _split_semicolons(text: str) -> list[str]:
    return [part.strip() for part in (text or "").split(";") if part.strip()]


def _first_value(*values: Any) -> str:
    for value in values:
        if value not in (None, "", []):
            text = str(value).strip()
            if text:
                return text
    return ""


def _infer_program_code(text: str) -> str | None:
    match = re.search(r"\b([A-ZА-Я]\d{2,4}|[78][MDМД]\d{5}|B\d{3}|В\d{3})\b", text or "", flags=re.IGNORECASE)
    return match.group(1).upper().replace("В", "B") if match else None


def _split_code_name(value: Any) -> tuple[str, str] | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.match(r"^\s*([A-Za-zА-Яа-яӘәІіҢңҒғҮүҰұҚқӨөҺһ]\d[\w.-]*|\d+[A-Za-zА-Яа-яӘәІіҢңҒғҮүҰұҚқӨөҺһ]?\d*)\s+(.+)$", text)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _metadata_fields(metadata: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    fields = dict(metadata.get("fields") or {})
    system_fields = dict(metadata.get("system_fields") or {})
    for key in (
        "education_area_code",
        "education_area_name",
        "education_area",
        "training_direction_code",
        "training_direction_name",
        "training_direction",
        "program_group_code",
        "program_group_name",
        "program_group",
        "program_code",
        "program_name",
        "program",
    ):
        if key not in system_fields and fields.get(key):
            system_fields[key] = fields[key]
    for composite_key, code_key, name_key in (
        ("education_area", "education_area_code", "education_area_name"),
        ("training_direction", "training_direction_code", "training_direction_name"),
        ("program_group", "program_group_code", "program_group_name"),
        ("program", "program_code", "program_name"),
    ):
        split = _split_code_name(system_fields.get(composite_key))
        if split:
            system_fields.setdefault(code_key, split[0])
            system_fields.setdefault(name_key, split[1])
        if system_fields.get(code_key) and system_fields.get(name_key):
            system_fields.setdefault(composite_key, f"{system_fields[code_key]} {system_fields[name_key]}")
    for key, value in fields.items():
        lowered = str(key or "").lower()
        if "program" not in lowered and "programma" not in lowered and "obrazovat" not in lowered:
            continue
        if "code" in lowered or "kod" in lowered or "name" in lowered or "nazv" in lowered or "programma" in lowered:
            system_fields.setdefault("program_name", value)
            system_fields.setdefault("program_group", value)
    return fields, system_fields


def load_program_catalog(entries_path: str = KNOWLEDGE_ENTRIES_PATH) -> List[Program]:
    programs: dict[str, Program] = {}
    for record in _jsonl_read(entries_path):
        metadata = dict(record.get("metadata", {}) or {})
        class_name = str(record.get("domain") or record.get("class_name") or metadata.get("domain") or "")
        schema = str(record.get("schema") or "")
        if class_name != "programs" and metadata.get("domain") != "programs":
            continue
        title = str(record.get("title") or "").strip()
        text = str(record.get("text") or "").strip()
        if not title and not text:
            continue
        fields, system_fields = _metadata_fields(metadata)
        code = _first_value(system_fields.get("program_code"), fields.get("program_code"), _infer_program_code(text), system_fields.get("program_group_code"))
        name = _first_value(
            system_fields.get("program_name"),
            system_fields.get("program"),
            system_fields.get("program_group_name"),
            system_fields.get("program_group"),
            fields.get("program_name"),
            fields.get("program_group"),
            title,
            _split_semicolons(text)[0] if text else "",
        )
        if not name:
            continue
        key = code or name
        program = programs.setdefault(key, Program(name=name, code=code or None))
        group_name = _first_value(system_fields.get("program_group_name"), system_fields.get("program_group"))
        if group_name and not program.group_name:
            program.group_name = group_name
        if code and not program.code:
            program.code = code
        if name and (not program.name or program.name == key):
            program.name = name
        program.description = text or program.description
        if class_name == "programs" or schema in {"program_entry", "program_text", "generic_text"}:
            row_bits = _split_semicolons(text)
            lowered = [bit.lower() for bit in row_bits]
            field_tags = [str(value).lower() for value in fields.values() if value]
            program.tags = sorted(set(program.tags + lowered + field_tags))
            program.career_paths = sorted(set(program.career_paths + row_bits[1:4]))
            if not program.ent_subjects:
                program.ent_subjects = row_bits[1:3]
    return sorted(programs.values(), key=lambda item: item.name.lower())
