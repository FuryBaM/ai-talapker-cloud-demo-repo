from __future__ import annotations

import json
from pathlib import Path

from core.config import KNOWLEDGE_CATALOG_PATH
from core.schemas import DomainDefinition, KnowledgeCatalog, SchemaDefinition, SchemaFieldDefinition, SystemFieldDefinition


DEFAULT_DOMAINS = [
    DomainDefinition(name="programs", description="Educational programs and specializations"),
    DomainDefinition(name="tuition", description="Tuition and fees"),
    DomainDefinition(name="scores", description="Threshold scores and exam requirements"),
    DomainDefinition(name="timeline", description="Admission and academic timelines"),
    DomainDefinition(name="contacts", description="Contacts, addresses, and communication channels"),
    DomainDefinition(name="housing", description="Dormitory and housing information"),
    DomainDefinition(name="benefits", description="Benefits, discounts, and grants"),
    DomainDefinition(name="documents", description="Required documents and admission forms"),
    DomainDefinition(name="university_info", description="General university information"),
]

DEFAULT_SYSTEM_FIELDS = [
    SystemFieldDefinition(
        key="domain",
        label="Домен",
        type="enum",
        values=["programs", "tuition", "admission_rules", "housing", "contacts"],
    ),
    SystemFieldDefinition(
        key="education_level",
        label="Уровень образования",
        type="enum",
        values=["bachelor", "master", "doctorate", "college", "military_department"],
    ),
    SystemFieldDefinition(
        key="language",
        label="Язык",
        type="enum",
        values=["ru", "kk", "en"],
    ),
    SystemFieldDefinition(key="education_area_code", label="Код области образования", type="text", applies_to=["field", "column", "paragraph"]),
    SystemFieldDefinition(key="education_area_name", label="Область образования", type="text", applies_to=["field", "column", "paragraph"]),
    SystemFieldDefinition(key="education_area", label="Код и классификация области образования", type="text", applies_to=["field", "column", "paragraph"]),
    SystemFieldDefinition(key="training_direction_code", label="Код направления подготовки", type="text", applies_to=["field", "column", "paragraph"]),
    SystemFieldDefinition(key="training_direction_name", label="Направление подготовки", type="text", applies_to=["field", "column", "paragraph"]),
    SystemFieldDefinition(key="training_direction", label="Код и классификация направления подготовки", type="text", applies_to=["field", "column", "paragraph"]),
    SystemFieldDefinition(key="program_group_code", label="Шифр группы образовательных программ", type="text", applies_to=["field", "column", "paragraph"]),
    SystemFieldDefinition(key="program_group_name", label="Наименование группы образовательных программ", type="text", applies_to=["field", "column", "paragraph"]),
    SystemFieldDefinition(
        key="program_code",
        label="Шифр образовательной программы",
        type="text",
        applies_to=["field", "column", "paragraph"],
    ),
    SystemFieldDefinition(
        key="program_name",
        label="Наименование образовательной программы",
        type="text",
        applies_to=["field", "column", "paragraph"],
    ),
    SystemFieldDefinition(
        key="program_group",
        label="Шифр и наименование группы образовательных программ",
        type="text",
        applies_to=["field", "column", "paragraph"],
    ),
    SystemFieldDefinition(
        key="program",
        label="Шифр и наименование образовательной программы",
        type="text",
        applies_to=["field", "column", "paragraph"],
    ),
    SystemFieldDefinition(
        key="tuition_price",
        label="Стоимость обучения",
        type="text",
        applies_to=["field", "column", "paragraph"],
    ),
]


DEFAULT_SCHEMAS = [
    SchemaDefinition(
        name="generic_text",
        handler="generic_text",
        description="Fallback plain text entry",
        fields=[SchemaFieldDefinition(name="text", label="Text", required=True)],
    ),
    SchemaDefinition(
        name="sectioned_text",
        handler="sectioned_text",
        description="Structured text split by sections",
        fields=[
            SchemaFieldDefinition(name="title", label="Section Title", preset="title"),
            SchemaFieldDefinition(name="text", label="Section Text", required=True, preset="rich_text"),
        ],
    ),
    SchemaDefinition(
        name="program_entry",
        handler="program_entry",
        description="Program row extracted from table",
        fields=[
            SchemaFieldDefinition(name="name", label="Program Name", required=True, preset="program_name", system_field="program_name"),
            SchemaFieldDefinition(name="text", label="Description", required=True, preset="rich_text"),
        ],
    ),
    SchemaDefinition(
        name="program_text",
        handler="program_text",
        description="Program text description",
        fields=[
            SchemaFieldDefinition(name="title", label="Program Title", preset="title"),
            SchemaFieldDefinition(name="text", label="Program Text", required=True, preset="rich_text"),
        ],
    ),
    SchemaDefinition(
        name="program_tuition_entry",
        handler="program_tuition_entry",
        description="Tuition row per program",
        fields=[
            SchemaFieldDefinition(name="name", label="Program Name", preset="program_name", system_field="program_name"),
            SchemaFieldDefinition(name="amount", label="Amount", preset="currency", system_field="tuition_price", validation={"type": "number", "min": 0}),
            SchemaFieldDefinition(name="text", label="Embedding Text", required=True, preset="search_text"),
        ],
    ),
    SchemaDefinition(
        name="dormitory_tuition_entry",
        handler="dormitory_tuition_entry",
        description="Dormitory price row",
        fields=[
            SchemaFieldDefinition(name="housing_type", label="Housing Type", preset="housing_type"),
            SchemaFieldDefinition(name="amount", label="Amount", preset="currency", validation={"type": "number", "min": 0}),
            SchemaFieldDefinition(name="text", label="Embedding Text", required=True, preset="search_text"),
        ],
    ),
    SchemaDefinition(
        name="tuition_text",
        handler="tuition_text",
        description="Tuition-related text",
        fields=[SchemaFieldDefinition(name="text", label="Tuition Text", required=True, preset="search_text")],
    ),
    SchemaDefinition(
        name="timeline_entry",
        handler="timeline_entry",
        description="Timeline row/event",
        fields=[
            SchemaFieldDefinition(name="event", label="Event", required=True, preset="timeline_event"),
            SchemaFieldDefinition(name="date_range", label="Date Range", preset="date_range"),
            SchemaFieldDefinition(name="text", label="Text", required=True, preset="search_text"),
        ],
    ),
    SchemaDefinition(
        name="timeline_text",
        handler="timeline_text",
        description="Timeline descriptive text",
        fields=[SchemaFieldDefinition(name="text", label="Timeline Text", required=True, preset="search_text")],
    ),
]


def _default_catalog() -> KnowledgeCatalog:
    return KnowledgeCatalog(domains=DEFAULT_DOMAINS, schemas=DEFAULT_SCHEMAS, system_fields=DEFAULT_SYSTEM_FIELDS)


def save_catalog(catalog: KnowledgeCatalog, catalog_path: str = KNOWLEDGE_CATALOG_PATH) -> None:
    target = Path(catalog_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(catalog.model_dump(by_alias=True), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_catalog(catalog_path: str = KNOWLEDGE_CATALOG_PATH) -> KnowledgeCatalog:
    path = Path(catalog_path)
    if not path.exists():
        catalog = _default_catalog()
        save_catalog(catalog, catalog_path=catalog_path)
        return catalog
    raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    catalog = KnowledgeCatalog(**raw)
    if not catalog.domains:
        catalog.domains = list(DEFAULT_DOMAINS)
    if not catalog.schemas:
        catalog.schemas = list(DEFAULT_SCHEMAS)
    if not catalog.system_fields:
        catalog.system_fields = list(DEFAULT_SYSTEM_FIELDS)
    else:
        defaults = {field.key: field for field in DEFAULT_SYSTEM_FIELDS}
        merged = {field.key: field for field in catalog.system_fields if field.key.strip()}
        for key, default in defaults.items():
            current = merged.get(key)
            if current:
                current.label = default.label
                current.field_type = default.field_type
                current.values = list(default.values)
                current.applies_to = list(default.applies_to)
            else:
                merged[key] = default
        catalog.system_fields = list(merged.values())
    return catalog


def upsert_catalog(catalog: KnowledgeCatalog, catalog_path: str = KNOWLEDGE_CATALOG_PATH) -> KnowledgeCatalog:
    normalized = KnowledgeCatalog(
        domains=sorted(
            {domain.name: domain for domain in catalog.domains if domain.name.strip()}.values(),
            key=lambda item: item.name,
        ),
        schemas=sorted(
            {schema.name: schema for schema in catalog.schemas if schema.name.strip()}.values(),
            key=lambda item: item.name,
        ),
        system_fields=sorted(
            {field.key: field for field in (catalog.system_fields or DEFAULT_SYSTEM_FIELDS) if field.key.strip()}.values(),
            key=lambda item: item.key,
        ),
    )
    save_catalog(normalized, catalog_path=catalog_path)
    return load_catalog(catalog_path=catalog_path)


def resolve_schema_handler(schema_name: str, catalog_path: str = KNOWLEDGE_CATALOG_PATH) -> str:
    catalog = load_catalog(catalog_path=catalog_path)
    for schema in catalog.schemas:
        if schema.name == schema_name and schema.enabled:
            return schema.handler
    return "generic_text"
