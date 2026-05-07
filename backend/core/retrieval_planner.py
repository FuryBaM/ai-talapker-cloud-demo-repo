from __future__ import annotations

import json
from typing import Any

from core.config import DEFAULT_RETRIEVAL_DOMAINS
from core.generation import generate_from_messages
from core.text_utils import extract_json_object, extract_json_text


RAG_DOMAINS = {
    "programs",
    "documents",
    "scores",
    "tuition",
    "benefits",
    "timeline",
    "housing",
    "contacts",
    "university_info",
}
ALLOWED_SCHEMAS = {
    "program_entry",
    "program_text",
    "program_tuition_entry",
    "dormitory_tuition_entry",
    "tuition_text",
    "sectioned_text",
    "generic_text",
    "timeline_entry",
    "timeline_text",
}


def llm_preferred_domains(query: str, history_text: str = "") -> list[str]:
    messages = [
        {
            "role": "system",
            "content": (
                "Choose the most relevant RAG domains for a university question. "
                "Use only this domain set: programs, documents, scores, tuition, benefits, timeline, housing, contacts, university_info. "
                "Return JSON only in the form {\"domains\":[\"domain1\",\"domain2\",...]}. "
                "Choose 0 to 3 domains. Prefer precise domains over general. "
                "Questions about discounts, concessions, reduced payment, or social categories should usually prefer benefits. "
                "Questions about who can get housing or be recognized as needing housing should usually prefer housing."
            ),
        },
        {
            "role": "user",
            "content": f"Question:\n{query}\n\nRecent history:\n{history_text}",
        },
    ]
    raw = generate_from_messages(messages, max_new_tokens=90, ctx_texts=None).strip()
    payload_text = extract_json_text(raw)
    if not payload_text:
        return []
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return []
    domains = payload.get("domains", [])
    if not isinstance(domains, list):
        return []
    normalized: list[str] = []
    for value in domains:
        domain = str(value or "").strip().lower()
        if domain in RAG_DOMAINS and domain not in normalized:
            normalized.append(domain)
        if len(normalized) >= 3:
            break
    return normalized


def llm_retrieval_filters(query: str, history_text: str = "") -> dict[str, Any]:
    messages = [
        {
            "role": "system",
            "content": (
                "Choose retrieval filters for a university knowledge base. "
                "Return JSON only with keys: domains, schemas, education_level, language. "
                "Allowed domains: programs, documents, scores, tuition, benefits, timeline, housing, contacts, university_info. "
                "Allowed schemas: program_entry, program_text, program_tuition_entry, dormitory_tuition_entry, tuition_text, sectioned_text, generic_text, timeline_entry, timeline_text. "
                "education_level can be bachelor, master, phd, or null. "
                "language can be ru, kk, en, or null. "
                "Choose 1 to 3 domains when possible."
            ),
        },
        {
            "role": "user",
            "content": f"Question:\n{query}\n\nRecent history:\n{history_text}",
        },
    ]
    raw = generate_from_messages(messages, max_new_tokens=120, ctx_texts=None).strip()
    payload = extract_json_object(raw)
    domains = [str(item).strip().lower() for item in payload.get("domains", []) if str(item).strip().lower() in RAG_DOMAINS]
    schemas = [str(item).strip().lower() for item in payload.get("schemas", []) if str(item).strip().lower() in ALLOWED_SCHEMAS]
    education_level = str(payload.get("education_level") or "").strip().lower() or None
    if education_level not in {"bachelor", "master", "phd"}:
        education_level = None
    language = str(payload.get("language") or "").strip().lower() or None
    if language not in {"ru", "kk", "en"}:
        language = None
    return {
        "domains": domains or list(DEFAULT_RETRIEVAL_DOMAINS),
        "schemas": schemas,
        "education_level": education_level,
        "language": language,
    }


def payload_context_snippet(payload: dict[str, Any]) -> str:
    return str(payload.get("text") or "").strip()
