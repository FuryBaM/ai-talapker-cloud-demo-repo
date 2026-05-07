from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from core.config import INPUT_DATA_DIR, KNOWLEDGE_REGISTRY_PATH, REBUILD_CONFIG
from core.schemas import KnowledgeRegistrySource
from core.security import REGISTRY_SOURCE_EXTENSIONS


DOMAIN_CLASSES = {
    "programs",
    "tuition",
    "scores",
    "timeline",
    "contacts",
    "housing",
    "benefits",
    "documents",
    "university_info",
}


DEFAULT_SCHEMA_BY_CLASS = {
    "programs": "program_text",
    "tuition": "tuition_text",
    "scores": "generic_text",
    "timeline": "timeline_text",
    "contacts": "generic_text",
    "housing": "sectioned_text",
    "benefits": "sectioned_text",
    "documents": "sectioned_text",
    "university_info": "generic_text",
}


def _slug(value: str) -> str:
    allowed = []
    for char in (value or "").lower():
        if char.isalnum():
            allowed.append(char)
        else:
            allowed.append("_")
    return "".join(allowed).strip("_") or "source"


def _guess_class(path: str) -> str:
    hay = path.lower()
    ordered = [
        ("housing", ["общежит", "заселен", "проживан", "hostel"]),
        ("tuition", ["прейскурант", "стоимост", "оплат", "tuition"]),
        ("scores", ["порог", "балл", "ент", "ұбт", "score"]),
        ("timeline", ["хронолог", "календар", "срок", "timeline"]),
        ("contacts", ["контакт", "телефон", "email", "адрес"]),
        ("benefits", ["льгот", "скид", "daryn", "iqanat", "серпін", "серпин"]),
        ("documents", ["документ", "перечень", "заявлен", "справк"]),
        ("programs", ["образовательн", "программ", "оп ", "кафедр", "специальност"]),
        ("university_info", ["правила", "университет", "сагинов", "карганд", "поступ"]),
    ]
    for class_name, markers in ordered:
        if any(marker in hay for marker in markers):
            return class_name
    return "university_info"


def _guess_education_level(path: str) -> str | None:
    hay = path.lower()
    if "бакалав" in hay:
        return "bachelor"
    if "магист" in hay:
        return "master"
    if "докторан" in hay or "phd" in hay:
        return "phd"
    return None


def _guess_language(path: str) -> str | None:
    hay = path.lower()
    if any(marker in hay for marker in ["_ru", "-ru", "рус", "rus"]):
        return "ru"
    if any(marker in hay for marker in ["_kk", "-kk", "каз", "қаз", "kaz"]):
        return "kk"
    if any(marker in hay for marker in ["_en", "-en", "eng", "english"]):
        return "en"
    return None


def bootstrap_registry(input_dir: str = INPUT_DATA_DIR) -> list[KnowledgeRegistrySource]:
    sources: list[KnowledgeRegistrySource] = []
    for path in sorted(Path(input_dir).rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in REGISTRY_SOURCE_EXTENSIONS:
            continue
        rel = str(path.relative_to(input_dir)).replace("\\", "/")
        class_name = _guess_class(rel)
        sources.append(
            KnowledgeRegistrySource(
                source_id=_slug(path.stem),
                path=rel,
                class_name=class_name,
                schema=DEFAULT_SCHEMA_BY_CLASS.get(class_name, "generic_text"),
                education_level=_guess_education_level(rel),
                language=_guess_language(rel),
                enabled=True,
            )
        )
    return sources


def save_registry(sources: Iterable[KnowledgeRegistrySource], registry_path: str = KNOWLEDGE_REGISTRY_PATH) -> None:
    target = Path(registry_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [source.model_dump(by_alias=True) for source in sources]
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_registry(registry_path: str = KNOWLEDGE_REGISTRY_PATH, input_dir: str = INPUT_DATA_DIR) -> list[KnowledgeRegistrySource]:
    path = Path(registry_path)
    if not path.exists():
        sources = bootstrap_registry(input_dir=input_dir)
        save_registry(sources, registry_path=registry_path)
        return sources

    raw = json.loads(path.read_text(encoding="utf-8") or "[]")
    sources = [KnowledgeRegistrySource(**item) for item in raw]

    filtered_sources = [
        source for source in sources
        if Path(source.path).suffix.lower() in REGISTRY_SOURCE_EXTENSIONS
    ]
    changed_by_filter = len(filtered_sources) != len(sources)
    sources = filtered_sources

    changed = changed_by_filter
    if REBUILD_CONFIG.get("registry_bootstrap", True):
        existing = {source.path for source in sources}
        bootstrapped = bootstrap_registry(input_dir=input_dir)
        for source in bootstrapped:
            if source.path not in existing:
                sources.append(source)
                changed = True

    if changed:
        save_registry(sources, registry_path=registry_path)
    return sources


def upsert_registry(sources: list[KnowledgeRegistrySource], registry_path: str = KNOWLEDGE_REGISTRY_PATH) -> list[KnowledgeRegistrySource]:
    save_registry(sources, registry_path=registry_path)
    return load_registry(registry_path=registry_path)
