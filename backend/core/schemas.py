from typing import Any, Dict, List, Optional

import numpy as np
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class Chunk(BaseModel):
    path_to_file: str
    text: str
    chunk_id: str = ""
    logical_group_id: str = ""
    entry_type: str = ""
    embedding: np.ndarray | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RagDocument(BaseModel):
    doc_id: str
    domain: str
    title: str
    text: str
    embedding_text: str | None = None
    source_file: str
    source_url: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RagChunk(BaseModel):
    chunk_id: str
    doc_id: str
    domain: str
    title: str
    text: str
    embedding_text: str | None = None
    source_file: str
    source_url: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EntryFormat(BaseModel):
    format: str = "single_text_entry"
    header_row: int | None = None
    data_start_row: int | None = None
    title_column: int | None = None
    text_columns: List[int] = Field(default_factory=list)
    field_labels: List[Dict[str, Any]] = Field(default_factory=list)
    ignored_rows: List[int] = Field(default_factory=list)
    selected_paragraphs: List[int] = Field(default_factory=list)
    settings: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def coerce_legacy_format(cls, data: Any) -> Any:
        if isinstance(data, str):
            return {"format": data}
        if not isinstance(data, dict):
            return data
        normalized = dict(data)
        settings = dict(normalized.get("settings") or {})
        for key in (
            "sheet_name",
            "title",
            "source_column",
            "note_column",
            "field_map",
            "schema_field_map",
            "fields",
            "table_profile",
            "source_profile",
            "metadata",
        ):
            if key in normalized and key not in settings:
                settings[key] = normalized.pop(key)
        normalized["settings"] = settings
        if "type" in normalized and "format" not in normalized:
            normalized["format"] = normalized["type"]
        return normalized


class KnowledgeSourceItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    item_id: str = ""
    domain: str = Field(default="", validation_alias=AliasChoices("domain", "class_name"))
    entry_type: str = Field(default="knowledge_entry", validation_alias=AliasChoices("entry_type", "schema"))
    title: str | None = None
    entry_format: EntryFormat = Field(default_factory=EntryFormat)
    education_level: str | None = None
    language: str | None = None
    enabled: bool = True
    source_url: str | None = None
    notes: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @property
    def class_name(self) -> str:
        return self.domain

    @property
    def schema_name(self) -> str:
        return self.entry_type


class KnowledgeRegistrySource(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_id: str
    path: str
    source_type: str | None = None
    origin: str | None = None
    class_name: str = ""
    schema_name: str = Field(default="knowledge_entry", alias="schema")
    education_level: str | None = None
    language: str | None = None
    enabled: bool = True
    source_url: str | None = None
    notes: str | None = None
    mapping: Dict[str, Any] = Field(default_factory=dict)
    items: List[KnowledgeSourceItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_items(self) -> "KnowledgeRegistrySource":
        if self.items and isinstance((self.mapping or {}).get("sheet_mappings"), dict):
            has_sheet_items = any("__sheet__" in str(item.item_id or "") for item in self.items)
            if not has_sheet_items:
                self.items = self._legacy_items()
                return self
        if self.items:
            normalized: list[KnowledgeSourceItem] = []
            for index, item in enumerate(self.items, start=1):
                if not item.item_id:
                    item.item_id = f"{self.source_id}_item_{index}"
                if not item.domain and self.class_name:
                    item.domain = self.class_name
                if not item.entry_type and self.schema_name:
                    item.entry_type = self.schema_name
                if item.education_level is None:
                    item.education_level = self.education_level
                if item.language is None:
                    item.language = self.language
                if item.source_url is None:
                    item.source_url = self.source_url
                normalized.append(item)
            self.items = normalized
            return self

        self.items = self._legacy_items()
        return self

    def _legacy_item(self) -> KnowledgeSourceItem:
        mapping = dict(self.mapping or {})
        entry_format = _entry_format_from_legacy(self.schema_name, mapping)
        return KnowledgeSourceItem(
            item_id=self.source_id,
            domain=self.class_name,
            schema=self.schema_name or "knowledge_entry",
            title=str(mapping.get("title") or self.notes or "") or None,
            entry_format=entry_format,
            education_level=self.education_level,
            language=self.language,
            enabled=True,
            source_url=self.source_url,
            notes=self.notes,
            metadata={"legacy_source_schema": self.schema_name} if self.schema_name else {},
        )

    def _legacy_items(self) -> List[KnowledgeSourceItem]:
        mapping = dict(self.mapping or {})
        sheet_mappings = mapping.get("sheet_mappings")
        if not isinstance(sheet_mappings, dict) or not sheet_mappings:
            return [self._legacy_item()]

        items: list[KnowledgeSourceItem] = []
        for sheet_name, sheet_mapping in sheet_mappings.items():
            local_mapping = dict(sheet_mapping or {})
            local_mapping.setdefault("sheet_name", sheet_name)
            domain = str(local_mapping.get("domain") or local_mapping.get("class_name") or self.class_name or "")
            entry_type = str(local_mapping.get("entry_type") or local_mapping.get("schema") or self.schema_name or "knowledge_entry")
            item_id = f"{self.source_id}__sheet__{_slug(str(sheet_name or 'sheet'))}"
            items.append(
                KnowledgeSourceItem(
                    item_id=item_id,
                    domain=domain,
                    entry_type=entry_type,
                    title=str(local_mapping.get("title") or "") or None,
                    entry_format=_entry_format_from_legacy(entry_type, local_mapping),
                    education_level=local_mapping.get("education_level") or self.education_level,
                    language=local_mapping.get("language") or self.language,
                    enabled=True,
                    source_url=self.source_url,
                    notes=local_mapping.get("notes") or self.notes,
                    metadata={"sheet_name": sheet_name},
                )
            )
        return items


class KnowledgeEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entry_id: str
    source_id: str
    class_name: str
    domain: str = ""
    schema_name: str = Field(alias="schema")
    education_level: str | None = None
    language: str | None = None
    title: str
    text: str
    embedding_text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_file: str
    source_url: str | None = None

    @model_validator(mode="after")
    def sync_domain(self) -> "KnowledgeEntry":
        if not self.domain:
            self.domain = self.class_name
        if not self.class_name:
            self.class_name = self.domain
        return self


class DomainDefinition(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True


class SystemFieldDefinition(BaseModel):
    key: str
    label: str = ""
    field_type: str = Field(default="text", alias="type")
    values: List[str] = Field(default_factory=list)
    applies_to: List[str] = Field(default_factory=list)


class SchemaFieldDefinition(BaseModel):
    name: str
    label: str = ""
    field_type: str = "text"
    required: bool = False
    description: str = ""
    preset: str = ""
    system_field: str = ""
    validation: Dict[str, Any] = Field(default_factory=dict)


class SchemaDefinition(BaseModel):
    name: str
    handler: str
    description: str = ""
    enabled: bool = True
    fields: List[SchemaFieldDefinition] = Field(default_factory=list)


class KnowledgeCatalog(BaseModel):
    domains: List[DomainDefinition] = Field(default_factory=list)
    schemas: List[SchemaDefinition] = Field(default_factory=list)
    system_fields: List[SystemFieldDefinition] = Field(default_factory=list)


class CuratedEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entry_id: str
    source_id: str
    class_name: str
    domain: str = ""
    schema_name: str = Field(alias="schema")
    title: str
    text: str
    embedding_text: str
    education_level: str | None = None
    language: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_file: str = ""
    source_url: str | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def sync_domain(self) -> "CuratedEntry":
        if not self.domain:
            self.domain = self.class_name
        if not self.class_name:
            self.class_name = self.domain
        return self


class Ask(BaseModel):
    question: str
    use_llm: bool
    lang: str = "ru"
    session_id: Optional[str] = None
    allow_web_search: bool = False


class Answer(BaseModel):
    answer: str


class SuggestHistoryItem(BaseModel):
    role: str
    content: str


class SuggestRequest(BaseModel):
    lang: str = "ru"
    count: int = 5
    use_llm: bool = True
    history: List[SuggestHistoryItem] = Field(default_factory=list)


class SuggestResponse(BaseModel):
    questions: List[str]


class ChatReplyReference(BaseModel):
    id: str = ""
    role: str = "user"
    content: str = Field(default="", max_length=1000)
    ts: Optional[int] = None


class ChatRequest(BaseModel):
    session_id: str
    message: str
    message_id: Optional[str] = None
    lang: str = "ru"
    use_llm: bool = True
    allow_web_search: bool = False
    reply_to: Optional[ChatReplyReference] = None


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    route: str
    profile_complete: bool


class RagRebuildRequest(BaseModel):
    rebuild_data: bool = True
    normalize: bool = False
    documents: bool = False
    chunks: bool = False
    index: bool = False


class RagRebuildResponse(BaseModel):
    ok: bool
    input_files: int
    output_files: int
    skipped_files: int
    documents_count: int
    chunks_count: int
    programs: int
    normalized: bool
    registry_sources: int = 0
    entries_count: int = 0
    documents_built: bool
    chunks_built: bool
    index_built: bool


class RegistryUpdateRequest(BaseModel):
    sources: List[KnowledgeRegistrySource]


class RegistryResponse(BaseModel):
    sources: List[KnowledgeRegistrySource]


class KnowledgeEntryPreviewResponse(BaseModel):
    entries: List[KnowledgeEntry]


class SearchDebugRequest(BaseModel):
    query: str
    top_k: int = 5
    domains: List[str] = Field(default_factory=list)
    schemas: List[str] = Field(default_factory=list)
    education_level: str | None = None
    language: str | None = None


class SearchDebugHit(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    score: float
    source_id: str
    class_name: str
    domain: str = ""
    schema_name: str = Field(alias="schema")
    title: str
    text: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def sync_domain(self) -> "SearchDebugHit":
        if not self.domain:
            self.domain = self.class_name
        if not self.class_name:
            self.class_name = self.domain
        return self


class SearchDebugResponse(BaseModel):
    hits: List[SearchDebugHit]


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: int


class CatalogResponse(BaseModel):
    catalog: KnowledgeCatalog


class SourcePreviewResponse(BaseModel):
    source: KnowledgeRegistrySource
    parsed: Dict[str, Any]


class SourceMappingUpdateRequest(BaseModel):
    source_id: str
    mapping: Dict[str, Any] = Field(default_factory=dict)
    class_name: str | None = None
    schema_name: str | None = Field(default=None, alias="schema")
    entry_format: EntryFormat | None = None
    items: List[KnowledgeSourceItem] | None = None
    education_level: str | None = None
    language: str | None = None
    notes: str | None = None

    model_config = ConfigDict(populate_by_name=True)


def _entry_format_from_legacy(schema_name: str, mapping: Dict[str, Any]) -> EntryFormat:
    if mapping:
        if mapping.get("source_type") == "xlsx" or mapping.get("extraction_mode") == "table":
            format_name = "row_as_entry"
        elif mapping.get("extraction_mode") == "semantic_text" or mapping.get("document_blocks") or mapping.get("logical_entries"):
            format_name = "semantic_document"
        elif mapping.get("selected_paragraphs"):
            format_name = "selected_paragraphs"
        else:
            format_name = "single_text_entry"
    elif schema_name in {"sectioned_text", "tuition_text", "timeline_text"}:
        format_name = "section_as_entry"
    elif schema_name in {"program_text", "program_entry", "program_tuition_entry", "dormitory_tuition_entry", "timeline_entry"}:
        format_name = "row_as_entry"
    else:
        format_name = "single_text_entry"

    return EntryFormat(
        format=format_name,
        header_row=mapping.get("header_row"),
        data_start_row=mapping.get("data_start_row"),
        title_column=mapping.get("title_column"),
        text_columns=list(mapping.get("text_columns") or []),
        field_labels=list(mapping.get("field_labels") or []),
        ignored_rows=list((mapping.get("table_profile") or {}).get("ignored_rows") or mapping.get("ignored_rows") or []),
        selected_paragraphs=list(mapping.get("selected_paragraphs") or []),
        settings={key: value for key, value in mapping.items() if key not in {"header_row", "data_start_row", "title_column", "text_columns", "field_labels", "ignored_rows", "selected_paragraphs"}},
    )


def _slug(value: str) -> str:
    chars = []
    for char in str(value or "").lower():
        chars.append(char if char.isalnum() else "_")
    return "".join(chars).strip("_") or "item"
