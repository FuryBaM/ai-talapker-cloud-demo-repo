from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Iterable

from core.knowledge_catalog import resolve_schema_handler
from core.normalize_input_data import clean_text, parse_source_content
from core.schemas import EntryFormat, KnowledgeEntry, KnowledgeRegistrySource, KnowledgeSourceItem
from core.security import safe_child_path


SYSTEM_FIELD_KEYS = {
    "domain",
    "education_level",
    "language",
    "education_area_code",
    "education_area_name",
    "education_area",
    "training_direction_code",
    "training_direction_name",
    "training_direction",
    "program_group_code",
    "program_group_name",
    "program_code",
    "program_name",
    "program_group",
    "program",
    "tuition_price",
}

COMPOSITE_SYSTEM_FIELDS = {
    "education_area": ("education_area_code", "education_area_name"),
    "training_direction": ("training_direction_code", "training_direction_name"),
    "program_group": ("program_group_code", "program_group_name"),
    "program": ("program_code", "program_name"),
}


def _entry(
    source: KnowledgeRegistrySource,
    item: KnowledgeSourceItem,
    entry_id: str,
    title: str,
    text: str,
    embedding_text: str,
    metadata: dict[str, Any],
) -> KnowledgeEntry:
    resolved_schema = clean_text(str(metadata.get("entry_type") or metadata.get("schema") or item.entry_type or "knowledge_entry"))
    merged_metadata = {
        **(item.metadata or {}),
        **metadata,
        "item_id": item.item_id or source.source_id,
        "entry_format": item.entry_format.format,
        "domain": item.domain,
        "schema": resolved_schema,
        "entry_type": resolved_schema,
    }
    return KnowledgeEntry(
        entry_id=entry_id,
        source_id=source.source_id,
        class_name=item.domain,
        domain=item.domain,
        schema=resolved_schema,
        education_level=item.education_level,
        language=item.language,
        title=title,
        text=clean_text(text),
        embedding_text=clean_text(embedding_text),
        metadata=merged_metadata,
        source_file=source.path,
        source_url=item.source_url or source.source_url,
    )


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _field_system_key(field: dict[str, Any]) -> str:
    explicit = str(field.get("system_field") or field.get("system_key") or "").strip()
    if explicit:
        return explicit
    name = str(field.get("name") or "").strip()
    preset = str(field.get("preset") or "").strip()
    if name in SYSTEM_FIELD_KEYS:
        return name
    if preset in SYSTEM_FIELD_KEYS:
        return preset
    if name in {"educational_program", "educational_program_name"}:
        return "program_name"
    return ""


def _split_code_name(value: str) -> tuple[str, str] | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.match(r"^\s*([A-Za-zА-Яа-яӘәІіҢңҒғҮүҰұҚқӨөҺһ]\d[\w.-]*|\d+[A-Za-zА-Яа-яӘәІіҢңҒғҮүҰұҚқӨөҺһ]?\d*)\s+(.+)$", text)
    if not match:
        return None
    return clean_text(match.group(1)), clean_text(match.group(2))


def _normalize_system_values(system_values: dict[str, str]) -> dict[str, str]:
    normalized = {key: value for key, value in system_values.items() if key in SYSTEM_FIELD_KEYS and clean_text(value)}
    for composite_key, (code_key, name_key) in COMPOSITE_SYSTEM_FIELDS.items():
        composite_value = normalized.get(composite_key)
        split = _split_code_name(composite_value or "")
        if split:
            normalized.setdefault(code_key, split[0])
            normalized.setdefault(name_key, split[1])
        code = normalized.get(code_key)
        name = normalized.get(name_key)
        if code and name:
            normalized.setdefault(composite_key, clean_text(f"{code} {name}"))
    return normalized


def _mapped_field_values(
    fields: list[dict[str, Any]],
    field_map: dict[str, Any],
    row: list[str],
    meta_values: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    meta_values = meta_values or {}
    values: dict[str, str] = {}
    system_values: dict[str, str] = {}
    for field in fields:
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        mapping = dict(field_map.get(name) or {})
        value = ""
        if mapping.get("kind") == "column":
            ref = str(mapping.get("ref") or "")
            column = _column_index(ref)
            if column is not None and column < len(row):
                value = clean_text(row[column])
        elif mapping.get("kind") == "cell":
            value = clean_text(meta_values.get(str(mapping.get("ref") or "").upper(), ""))
        if not value:
            continue
        values[name] = value
        system_key = _field_system_key(field)
        if system_key:
            system_values[system_key] = value
    return values, _normalize_system_values(system_values)


def _column_index(ref: Any) -> int | None:
    text = str(ref or "").strip().upper()
    if not text:
        return None
    if text.isdigit():
        value = int(text)
        return value - 1 if value > 0 else 0
    if not text.isalpha():
        return None
    result = 0
    for char in text:
        result = result * 26 + (ord(char) - 64)
    return result - 1 if result > 0 else None


def _sheet_meta_values(sheet: dict[str, Any], rows: list[list[str]], mapping: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    profile = dict(mapping.get("table_profile") or mapping.get("source_profile") or {})
    for item in profile.get("metadata") or []:
        address = str(item.get("address") or "").strip().upper()
        value = clean_text(str(item.get("value") or ""))
        if address and value:
            values[address] = value
    for field_mapping in dict(mapping.get("field_map") or mapping.get("schema_field_map") or {}).values():
        normalized = dict(field_mapping or {})
        if normalized.get("kind") != "cell":
            continue
        address = str(normalized.get("ref") or "").strip().upper()
        parsed = _parse_cell_address(address)
        if not parsed:
            continue
        row_index, column_index = parsed
        if row_index < len(rows) and column_index < len(rows[row_index]):
            values[address] = clean_text(rows[row_index][column_index])
    return values


def _parse_cell_address(address: str) -> tuple[int, int] | None:
    letters = []
    digits = []
    for char in str(address or "").strip().upper():
        if char.isalpha() and not digits:
            letters.append(char)
        elif char.isdigit():
            digits.append(char)
        else:
            return None
    if not letters or not digits:
        return None
    column = _column_index("".join(letters))
    row = int("".join(digits)) - 1
    if column is None or row < 0:
        return None
    return row, column


def _format_settings(item: KnowledgeSourceItem) -> dict[str, Any]:
    fmt = item.entry_format or EntryFormat()
    return {
        **dict(fmt.settings or {}),
        "header_row": fmt.header_row,
        "data_start_row": fmt.data_start_row,
        "title_column": fmt.title_column,
        "text_columns": fmt.text_columns,
        "field_labels": fmt.field_labels,
        "ignored_rows": fmt.ignored_rows,
        "selected_paragraphs": fmt.selected_paragraphs,
    }


def _mapped_excel_entries(source: KnowledgeRegistrySource, item: KnowledgeSourceItem, parsed: dict[str, Any]) -> list[KnowledgeEntry]:
    mapping = _format_settings(item)
    target_sheet = str(mapping.get("sheet_name") or "").strip()
    sheets = parsed.get("sheets", []) or []
    sheet = next((item for item in sheets if str(item.get("sheet_title") or "") == target_sheet), None)
    if not sheet and sheets:
        sheet = sheets[0]
    if not sheet:
        return []

    rows = list(sheet.get("rows", []) or [])
    field_definitions = [dict(field or {}) for field in (mapping.get("fields") or []) if isinstance(field, dict)]
    field_map = dict(mapping.get("field_map") or mapping.get("schema_field_map") or {})
    meta_values = _sheet_meta_values(sheet, rows, mapping)
    header_row_index = max(0, _safe_int(mapping.get("header_row"), 1) - 1)
    data_start_index = max(0, _safe_int(mapping.get("data_start_row"), header_row_index + 2) - 1)
    headers = rows[header_row_index] if header_row_index < len(rows) else []

    title_column = max(0, _safe_int(mapping.get("title_column"), 0))
    text_columns = [max(0, _safe_int(item, -1)) for item in (mapping.get("text_columns") or []) if str(item).strip()]
    if not text_columns:
        text_columns = [index for index in range(len(headers or [])) if index != title_column] or [title_column]
    source_column = mapping.get("source_column")
    note_column = mapping.get("note_column")
    try:
        source_column = None if source_column in {"", None} else int(source_column)
    except Exception:
        source_column = None
    try:
        note_column = None if note_column in {"", None} else int(note_column)
    except Exception:
        note_column = None

    field_labels = list(mapping.get("field_labels") or [])
    label_by_column = {}
    for item in field_labels:
        try:
            column = int(item.get("column"))
        except Exception:
            continue
        label = clean_text(str(item.get("label") or item.get("field") or ""))
        if label:
            label_by_column[column] = label

    entries: list[KnowledgeEntry] = []
    for row_index, row in enumerate(rows[data_start_index:], start=data_start_index + 1):
        if row_index in set(mapping.get("ignored_rows") or []):
            continue
        cleaned_row = [clean_text(cell) for cell in row]
        if not any(cleaned_row):
            continue
        title = cleaned_row[title_column] if title_column < len(cleaned_row) and cleaned_row[title_column] else f"{sheet.get('sheet_title') or source.source_id} #{row_index}"
        parts = []
        for column_index in text_columns:
            if column_index >= len(cleaned_row):
                continue
            value = cleaned_row[column_index]
            if not value:
                continue
            label = label_by_column.get(column_index) or (headers[column_index] if column_index < len(headers) else "")
            parts.append(f"{label}: {value}" if label else value)
        text = clean_text(". ".join(parts) or " ".join(value for value in cleaned_row if value))
        field_values, system_values = _mapped_field_values(field_definitions, field_map, cleaned_row, meta_values)
        if system_values.get("program_name"):
            title = system_values["program_name"]
        elif system_values.get("program_code") and title == f"{sheet.get('sheet_title') or source.source_id} #{row_index}":
            title = system_values["program_code"]
        if field_values:
            labeled_fields = []
            for field in field_definitions:
                name = str(field.get("name") or "").strip()
                if name not in field_values:
                    continue
                label = clean_text(str(field.get("label") or name))
                labeled_fields.append(f"{label}: {field_values[name]}" if label else field_values[name])
            text = clean_text(". ".join(labeled_fields) or text)
        metadata = {
            "record_type": "mapped_excel_row",
            "sheet_name": sheet.get("sheet_title"),
            "row_index": row_index,
            "headers": headers,
            "field_labels": field_labels,
            "fields": field_values,
            "system_fields": system_values,
        }
        if source_column is not None and source_column < len(cleaned_row) and cleaned_row[source_column]:
            metadata["mapped_source"] = cleaned_row[source_column]
        if note_column is not None and note_column < len(cleaned_row) and cleaned_row[note_column]:
            metadata["mapped_note"] = cleaned_row[note_column]
        entries.append(
            _entry(
                source,
                item,
                f"{item.item_id or source.source_id}_row_{row_index}",
                title,
                text,
                text,
                metadata,
            )
        )
    return entries


def _row_as_entry(source: KnowledgeRegistrySource, item: KnowledgeSourceItem, parsed: dict[str, Any]) -> list[KnowledgeEntry]:
    if parsed.get("sheets"):
        return _mapped_excel_entries(source, item, parsed)

    mapping = _format_settings(item)
    header_row_index = max(0, _safe_int(mapping.get("header_row"), 1) - 1)
    data_start_index = max(0, _safe_int(mapping.get("data_start_row"), header_row_index + 2) - 1)
    title_column = max(0, _safe_int(mapping.get("title_column"), 0))
    text_columns = [max(0, _safe_int(column, -1)) for column in (mapping.get("text_columns") or []) if str(column).strip()]
    ignored_rows = set(mapping.get("ignored_rows") or [])
    entries: list[KnowledgeEntry] = []

    for table_index, table in enumerate(parsed.get("tables", []) or [], start=1):
        rows = list(table or [])
        field_definitions = [dict(field or {}) for field in (mapping.get("fields") or []) if isinstance(field, dict)]
        field_map = dict(mapping.get("field_map") or mapping.get("schema_field_map") or {})
        headers = rows[header_row_index] if header_row_index < len(rows) else []
        selected_columns = text_columns or [index for index in range(len(headers or [])) if index != title_column] or [title_column]
        for row_index, row in enumerate(rows[data_start_index:], start=data_start_index + 1):
            if row_index in ignored_rows:
                continue
            cleaned_row = [clean_text(cell) for cell in row]
            if not any(cleaned_row):
                continue
            title = cleaned_row[title_column] if title_column < len(cleaned_row) and cleaned_row[title_column] else f"{item.title or parsed.get('title') or source.source_id} #{row_index}"
            parts = []
            for column_index in selected_columns:
                if column_index >= len(cleaned_row):
                    continue
                value = cleaned_row[column_index]
                if not value:
                    continue
                label = headers[column_index] if column_index < len(headers) else ""
                parts.append(f"{label}: {value}" if label else value)
            text = clean_text(". ".join(parts) or " ".join(value for value in cleaned_row if value))
            field_values, system_values = _mapped_field_values(field_definitions, field_map, cleaned_row)
            if system_values.get("program_name"):
                title = system_values["program_name"]
            elif system_values.get("program_code") and title == f"{item.title or parsed.get('title') or source.source_id} #{row_index}":
                title = system_values["program_code"]
            if field_values:
                labeled_fields = []
                for field in field_definitions:
                    name = str(field.get("name") or "").strip()
                    if name not in field_values:
                        continue
                    label = clean_text(str(field.get("label") or name))
                    labeled_fields.append(f"{label}: {field_values[name]}" if label else field_values[name])
                text = clean_text(". ".join(labeled_fields) or text)
            entries.append(
                _entry(
                    source,
                    item,
                    f"{item.item_id or source.source_id}_table_{table_index}_{row_index}",
                    title,
                    text,
                    text,
                    {
                        "record_type": "table_row",
                        "table_index": table_index,
                        "row_index": row_index,
                        "headers": headers,
                        "fields": field_values,
                        "system_fields": system_values,
                    },
                )
            )
    return entries


def _mapped_text_entries(source: KnowledgeRegistrySource, item: KnowledgeSourceItem, parsed: dict[str, Any]) -> list[KnowledgeEntry]:
    mapping = _format_settings(item)
    curated_title = clean_text(str(item.title or mapping.get("title") or parsed.get("title") or source.source_id))
    edited_text = clean_text(str(mapping.get("edited_text") or ""))
    selected_paragraphs = mapping.get("selected_paragraphs") or []
    paragraphs = list(parsed.get("paragraphs", []) or [])
    if edited_text:
        text = edited_text
    elif selected_paragraphs and paragraphs:
        blocks = [paragraphs[index] for index in selected_paragraphs if isinstance(index, int) and 0 <= index < len(paragraphs)]
        text = clean_text("\n\n".join(blocks))
    else:
        text = clean_text(parsed.get("text", "")) or clean_text("\n\n".join(paragraphs))
    if not text:
        return []
    return [
            _entry(
                source,
                item,
                f"{item.item_id or source.source_id}_selected_text",
                curated_title,
                text,
                text,
            {
                "record_type": "mapped_text",
                "selected_paragraphs": selected_paragraphs,
            },
        )
    ]



def _list_value(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [item for item in value if item not in {None, ""}]
    return [value]


def _block_level(block: dict[str, Any]) -> int | None:
    role = str(block.get("role") or "")
    if block.get("level"):
        try:
            return int(block.get("level"))
        except Exception:
            return None
    if role == "heading_1":
        return 1
    if role == "heading_2":
        return 2
    if role == "heading_3":
        return 3
    return None


def _is_list_like_text(value: str) -> bool:
    return bool(re.match(r"^\s*(?:[-•*–—]\s+|\d+[.)]\s+|[a-zа-я]\)\s+)", str(value or ""), flags=re.IGNORECASE))


def _strip_list_marker(value: str) -> str:
    return re.sub(r"^\s*(?:[-•*–—]\s+|\d+[.)]\s+|[a-zа-я]\)\s+)", "", str(value or "")).strip()


def _infer_semantic_entry_type(item: KnowledgeSourceItem, blocks: list[dict[str, Any]], extra: dict[str, Any] | None = None) -> str:
    explicit = clean_text(str((extra or {}).get("entry_type") or (extra or {}).get("schema") or ""))
    if explicit:
        return explicit
    title = clean_text(str((extra or {}).get("title") or ""))
    has_list_role = any(str(block.get("role") or "") == "list_item" or _is_list_like_text(str(block.get("text") or "")) for block in blocks)
    has_list_type = any("document_list" in _list_value(block.get("knowledge_types")) or "required_document" in _list_value(block.get("knowledge_types")) for block in blocks)
    list_title = bool(re.search(r"(?:перечень|список|пакет)\s+(?:документ|документов)|необходимые\s+документы", title, flags=re.IGNORECASE))
    if has_list_role or has_list_type or list_title:
        return "document_list"
    return item.entry_type or "knowledge_entry"


def _semantic_document_entries(source: KnowledgeRegistrySource, item: KnowledgeSourceItem, parsed: dict[str, Any]) -> list[KnowledgeEntry]:
    mapping = _format_settings(item)
    raw_blocks = mapping.get("document_blocks") or mapping.get("text_blocks") or []
    paragraphs = list(parsed.get("paragraphs", []) or [])
    if not raw_blocks:
        raw_blocks = [
            {"id": f"p_{index + 1}", "source_index": index, "text": paragraph, "original_text": paragraph, "role": "body"}
            for index, paragraph in enumerate(paragraphs)
        ]
    blocks: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_blocks):
        block = dict(raw or {})
        text = clean_text(str(block.get("text") or block.get("edited_text") or block.get("original_text") or ""))
        if not text:
            continue
        block["id"] = str(block.get("id") or block.get("block_id") or f"p_{index + 1}")
        block["text"] = text
        block["role"] = str(block.get("role") or "body")
        block["source_index"] = block.get("source_index", index)
        blocks.append(block)

    by_id = {str(block.get("id")): block for block in blocks}
    entries: list[KnowledgeEntry] = []

    def section_path_for(block_id: str) -> list[str]:
        path: list[str] = []
        for block in blocks:
            if str(block.get("id")) == block_id:
                break
            level = _block_level(block)
            if not level or block.get("role") == "ignore":
                continue
            path[level - 1 :] = []
            path.append(clean_text(str(block.get("text") or "")))
        return [item for item in path if item]

    def build_entry(entry_id: str, title: str, selected_blocks: list[dict[str, Any]], logical: bool, extra: dict[str, Any] | None = None) -> KnowledgeEntry | None:
        usable = [block for block in selected_blocks if block.get("role") != "ignore" and clean_text(str(block.get("text") or ""))]
        if not usable:
            return None
        first = usable[0]
        section_path = section_path_for(str(first.get("id")))
        final_title = clean_text(title) or clean_text(str((extra or {}).get("title") or "")) or (section_path[-1] if section_path else item.title or parsed.get("title") or source.source_id)
        entry_type = _infer_semantic_entry_type(item, usable, {**(extra or {}), "title": final_title})

        list_items: list[str] = []
        if entry_type == "document_list":
            for block in usable:
                role = str(block.get("role") or "body")
                if role.startswith("heading") or role in {"meta", "note"}:
                    continue
                text_value = _strip_list_marker(clean_text(str(block.get("text") or "")))
                if text_value:
                    list_items.append(text_value)

        parts: list[str] = []
        if list_items:
            parts = [final_title, *[f"- {value}" for value in list_items]]
        else:
            for block in usable:
                role = str(block.get("role") or "body")
                text_value = clean_text(str(block.get("text") or ""))
                if not text_value:
                    continue
                if role == "meta":
                    parts.append(f"Метаданные: {text_value}")
                elif role == "note":
                    parts.append(f"Примечание: {text_value}")
                elif role.startswith("heading"):
                    continue
                else:
                    parts.append(text_value)
        if not parts:
            parts = [clean_text(str(block.get("text") or "")) for block in usable if not str(block.get("role") or "").startswith("heading")]
        separator = "\n" if list_items else "\n\n"
        text = clean_text(separator.join(parts))
        if not text:
            return None
        domains = _list_value((extra or {}).get("domains")) or _list_value(first.get("domains")) or [item.domain]
        knowledge_types = _list_value((extra or {}).get("knowledge_types")) or _list_value(first.get("knowledge_types"))
        if entry_type == "document_list" and "document_list" not in knowledge_types:
            knowledge_types.append("document_list")
        system_marks = _list_value(first.get("system_fields")) or _list_value(first.get("system_marks"))
        system_values = {str(key): text for key in system_marks if str(key)}
        education_levels = _list_value((extra or {}).get("education_levels")) or _list_value(first.get("education_levels"))
        language = (extra or {}).get("language") or first.get("language") or item.language
        logical_group_id = clean_text(str((extra or {}).get("logical_group_id") or (entry_id if logical else "")))
        embedding_text = clean_text(f"{final_title}. Список: {'; '.join(list_items)}" if list_items else text)
        metadata = {
            "record_type": "semantic_document_entry" if logical else "semantic_document_section",
            "entry_type": entry_type,
            "schema": entry_type,
            "logical_group_id": logical_group_id or None,
            "expansion_policy": "full_logical_group" if entry_type == "document_list" else "entry_context",
            "list_items": list_items,
            "list_count": len(list_items),
            "block_ids": [str(block.get("id")) for block in usable],
            "source_indexes": [block.get("source_index") for block in usable],
            "section_path": section_path,
            "domains": domains,
            "knowledge_types": knowledge_types,
            "system_marks": system_marks,
            "system_fields": system_values,
            "education_levels": education_levels,
            "language": language,
        }
        if extra:
            metadata["logical_entry"] = {key: value for key, value in extra.items() if key not in {"block_ids", "parts", "blocks"}}
        return _entry(source, item, entry_id, final_title, text, embedding_text, metadata)

    logical_entries = mapping.get("logical_entries") or []
    for index, entry in enumerate(logical_entries, start=1):
        if not isinstance(entry, dict):
            continue
        block_ids = [str(value) for value in (entry.get("block_ids") or entry.get("parts") or entry.get("blocks") or [])]
        selected = [by_id[block_id] for block_id in block_ids if block_id in by_id]
        built = build_entry(
            str(entry.get("entry_id") or f"{item.item_id or source.source_id}_logical_{index}"),
            str(entry.get("title") or ""),
            selected,
            True,
            entry,
        )
        if built:
            entries.append(built)
    if entries:
        return entries

    path: list[str] = []
    current_title = item.title or parsed.get("title") or source.source_id
    current_blocks: list[dict[str, Any]] = []
    counter = 0

    def flush() -> None:
        nonlocal counter, current_blocks, current_title
        if not current_blocks:
            return
        counter += 1
        built = build_entry(f"{item.item_id or source.source_id}_section_{counter}", current_title, current_blocks, False)
        if built:
            entries.append(built)
        current_blocks = []

    for block in blocks:
        if block.get("role") == "ignore":
            continue
        level = _block_level(block)
        if level:
            flush()
            path[level - 1 :] = []
            path.append(clean_text(str(block.get("text") or "")))
            current_title = path[-1] if path else current_title
            continue
        current_blocks.append(block)
    flush()
    return entries or _mapped_text_entries(source, item, parsed)
def _paragraph_entries(source: KnowledgeRegistrySource, item: KnowledgeSourceItem, parsed: dict[str, Any]) -> list[KnowledgeEntry]:
    entries: list[KnowledgeEntry] = []
    paragraphs = parsed.get("paragraphs", [])
    for index, paragraph in enumerate(paragraphs, start=1):
        text = clean_text(paragraph)
        if not text:
            continue
        entries.append(
            _entry(
                source,
                item,
                f"{item.item_id or source.source_id}_p_{index}",
                item.title or parsed.get("title") or source.source_id,
                text,
                text,
                {"record_type": "paragraph", "paragraph_index": index},
            )
        )
    return entries


def _sectioned_text_entries(source: KnowledgeRegistrySource, item: KnowledgeSourceItem, parsed: dict[str, Any]) -> list[KnowledgeEntry]:
    entries = _paragraph_entries(source, item, parsed)
    if entries:
        return entries
    text = clean_text(parsed.get("text", ""))
    if not text:
        return []
    blocks = [clean_text(block) for block in text.split("\n\n") if clean_text(block)]
    return [
            _entry(
                source,
                item,
                f"{item.item_id or source.source_id}_section_{index}",
                item.title or parsed.get("title") or source.source_id,
                block,
                block,
            {"record_type": "section", "section_index": index},
        )
        for index, block in enumerate(blocks, start=1)
    ]


def _generic_text_entries(source: KnowledgeRegistrySource, item: KnowledgeSourceItem, parsed: dict[str, Any]) -> list[KnowledgeEntry]:
    text = clean_text(parsed.get("text", ""))
    if text:
        return [
            _entry(
                source,
                item,
                f"{item.item_id or source.source_id}_text",
                item.title or parsed.get("title") or source.source_id,
                text,
                text,
                {"record_type": "text"},
            )
        ]
    return _sectioned_text_entries(source, item, parsed)


def _iter_table_rows(parsed: dict[str, Any]) -> Iterable[tuple[str, int, int, list[str]]]:
    for sheet in parsed.get("sheets", []):
        sheet_title = sheet.get("sheet_title") or parsed.get("title") or ""
        rows = sheet.get("rows", [])
        for row_index, row in enumerate(rows, start=1):
            cleaned = [clean_text(cell) for cell in row if clean_text(cell) and clean_text(cell) != "в€…"]
            if cleaned:
                yield sheet_title, 1, row_index, cleaned
    for table_index, table in enumerate(parsed.get("tables", []), start=1):
        for row_index, row in enumerate(table, start=1):
            cleaned = [clean_text(cell) for cell in row if clean_text(cell) and clean_text(cell) != "в€…"]
            if cleaned:
                yield parsed.get("title") or "", table_index, row_index, cleaned


def _program_entries(source: KnowledgeRegistrySource, item: KnowledgeSourceItem, parsed: dict[str, Any]) -> list[KnowledgeEntry]:
    entries: list[KnowledgeEntry] = []
    for title, table_index, row_index, row in _iter_table_rows(parsed):
        program_name = row[0]
        text = "; ".join(row)
        entries.append(
            _entry(
                source,
                item,
                f"{item.item_id or source.source_id}_program_{table_index}_{row_index}",
                title or program_name,
                text,
                f"{program_name}. {' '.join(row)}",
                {"record_type": "program_row", "table_index": table_index, "row_index": row_index},
            )
        )
    if entries:
        return entries
    return _sectioned_text_entries(source, item, parsed)


def _timeline_entries(source: KnowledgeRegistrySource, item: KnowledgeSourceItem, parsed: dict[str, Any]) -> list[KnowledgeEntry]:
    entries: list[KnowledgeEntry] = []
    for title, table_index, row_index, row in _iter_table_rows(parsed):
        text = "; ".join(row)
        entries.append(
            _entry(
                source,
                item,
                f"{item.item_id or source.source_id}_timeline_{table_index}_{row_index}",
                item.title or title or parsed.get("title") or source.source_id,
                text,
                text,
                {"record_type": "timeline_row", "table_index": table_index, "row_index": row_index},
            )
        )
    return entries or _sectioned_text_entries(source, item, parsed)


def _tuition_entries(source: KnowledgeRegistrySource, item: KnowledgeSourceItem, parsed: dict[str, Any]) -> list[KnowledgeEntry]:
    entries: list[KnowledgeEntry] = []
    target_schema = item.entry_type
    for title, table_index, row_index, row in _iter_table_rows(parsed):
        text = "; ".join(row)
        embedding_text = text
        entries.append(
            _entry(
                source,
                item,
                f"{item.item_id or source.source_id}_tuition_{table_index}_{row_index}",
                item.title or title or parsed.get("title") or source.source_id,
                text,
                embedding_text,
                {
                    "record_type": target_schema,
                    "table_index": table_index,
                    "row_index": row_index,
                },
            )
        )
    return entries or _sectioned_text_entries(source, item, parsed)


SCHEMA_HANDLERS = {
    "generic_text": _generic_text_entries,
    "sectioned_text": _sectioned_text_entries,
    "program_text": _program_entries,
    "program_entry": _program_entries,
    "program_tuition_entry": _tuition_entries,
    "dormitory_tuition_entry": _tuition_entries,
    "tuition_text": _tuition_entries,
    "timeline_entry": _timeline_entries,
    "timeline_text": _timeline_entries,
}


FORMAT_HANDLERS = {
    "single_text_entry": _generic_text_entries,
    "selected_paragraphs": _mapped_text_entries,
    "semantic_document": _semantic_document_entries,
    "section_as_entry": _sectioned_text_entries,
    "row_as_entry": _row_as_entry,
}


def _handler_for_item(item: KnowledgeSourceItem):
    format_name = (item.entry_format.format or "").strip()
    if format_name in FORMAT_HANDLERS:
        return FORMAT_HANDLERS[format_name]
    return SCHEMA_HANDLERS.get(resolve_schema_handler(item.entry_type), _generic_text_entries)


def build_entries_for_source(source: KnowledgeRegistrySource, input_root: str) -> list[KnowledgeEntry]:
    source_path = safe_child_path(input_root, source.path)
    if not source.enabled or not source_path.exists():
        return []
    parsed = parse_source_content(source_path)
    entries: list[KnowledgeEntry] = []
    for item in source.items:
        if not item.enabled:
            continue
        handler = _handler_for_item(item)
        built = handler(source, item, parsed)
        entries.extend(built)
    if entries:
        return entries
    text = clean_text(parsed.get("text", ""))
    if not text:
        return []
    return [
        _entry(
            source,
            source.items[0],
            f"{source.source_id}_fallback",
            parsed.get("title") or source.source_id,
            text,
            text,
            {"record_type": "fallback_text"},
        )
    ]
