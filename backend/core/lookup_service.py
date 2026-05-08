from __future__ import annotations

from typing import Any, Callable

from core.agent_state import AgentState
from core.config import DEFAULT_RETRIEVAL_DOMAINS, LOOKUP_MAX_ITERATIONS
from core.generation import generate_from_messages
from core.knowledge_assets import get_index
from core.rag import find_passage


def is_contact_query(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in ["адрес", "контакт", "телефон", "где находится", "как найти", "местонахождение"])


def direct_contact_context(
    query: str,
    *,
    query_stems: Callable[[str], set[str]],
    text_structure_bonus: Callable[[str, str], int],
    payload_context_snippet: Callable[[dict[str, Any]], str],
) -> list[str]:
    if not is_contact_query(query):
        return []
    stems = query_stems(query)
    scored: list[tuple[int, str]] = []
    for payload in _scroll_payloads(limit=3000):
        text = str(payload.get("text") or "")
        if not text:
            continue
        haystack = text.lower()
        score = sum(1 for stem in stems if stem and stem in haystack)
        score += text_structure_bonus(query, text)
        if score > 0:
            scored.append((score, payload_context_snippet(payload)))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in scored[:3]]


def safe_university_fallback(lang: str, university_name_ru: str, university_name_en: str) -> str:
    if lang == "kk":
        return "Қолжетімді университет материалдарынан бұл сұраққа нақты жауап табылмады. Сұрақты нақтылап жазыңыз."
    if lang == "en":
        return f"I do not see exact information for this in the available materials of {university_name_en}. Please ask a more specific university-related question."
    return f"Я не вижу точной информации по этому вопросу в доступных материалах {university_name_ru}. Уточните, пожалуйста, запрос."


def _heuristic_lookup_queries(query: str, *, strip_leading_list_marker: Callable[[str], str]) -> list[str]:
    """Cheap fallback used when the LLM query rewriter would exceed context.

    Lookup query generation must never crash the graph. A too-long prompt to the
    local llama-server is worse than simply searching with deterministic variants.
    """
    text = str(query or "").strip()
    if not text:
        return []

    pieces: list[str] = []
    for raw in text.replace("?", "?\n").replace(";", ";\n").splitlines():
        item = strip_leading_list_marker(raw).strip(" -–—\t")
        if item and len(item) >= 8 and item not in pieces:
            pieces.append(item)
        if len(pieces) >= 3:
            break

    lower = text.lower()
    deterministic: list[str] = []
    if any(token in lower for token in ["математ", "информат", "бакалавр", "программ"]):
        deterministic.append("бакалавриат математика информатика образовательные программы")
    if any(token in lower for token in ["общежит", "заселен", "проживан"]):
        deterministic.append("общежитие заселение документы проживание")
    if any(token in lower for token in ["балл", "порог", "проходн"]):
        deterministic.append("пороговые баллы поступление образовательные программы")
    if any(token in lower for token in ["документ", "перечень", "справк"]):
        deterministic.append("перечень документов поступление")

    result: list[str] = []
    for item in [*deterministic, *pieces]:
        cleaned = " ".join(item.split())
        if cleaned and cleaned not in result:
            result.append(cleaned)
        if len(result) >= 3:
            break
    return result


def _compact_for_lookup_rewrite(query: str, max_chars: int = 2400) -> str:
    text = str(query or "").strip()
    if len(text) <= max_chars:
        return text
    # Keep the beginning and the end. For multi-question prompts the start often
    # contains the task type and the end often contains the newest concrete ask.
    head = text[: max_chars // 2].rstrip()
    tail = text[-max_chars // 2 :].lstrip()
    return f"{head}\n...\n{tail}"


def generate_lookup_queries(query: str, *, strip_leading_list_marker: Callable[[str], str]) -> list[str]:
    compact_query = _compact_for_lookup_rewrite(query)
    messages = [
        {
            "role": "system",
            "content": (
                "You rewrite a university-related user question into short retrieval queries for a local knowledge base. "
                "Return 3 lines max. Each line must be a short search query without explanations."
            ),
        },
        {"role": "user", "content": f"Question:\n{compact_query}"},
    ]
    try:
        raw = generate_from_messages(messages, max_new_tokens=60, ctx_texts=None)
    except Exception:
        return _heuristic_lookup_queries(query, strip_leading_list_marker=strip_leading_list_marker)

    queries = []
    for line in raw.splitlines():
        text = strip_leading_list_marker(line).strip()
        if text and text not in queries:
            queries.append(text)
    if not queries:
        return _heuristic_lookup_queries(query, strip_leading_list_marker=strip_leading_list_marker)
    return queries[:3]


def build_lookup_query_queue(
    query: str,
    *,
    normalize_spaces: Callable[[Any], str],
    strip_leading_list_marker: Callable[[str], str],
) -> list[str]:
    queue: list[str] = []
    for candidate in [query, *generate_lookup_queries(query, strip_leading_list_marker=strip_leading_list_marker)]:
        cleaned = normalize_spaces(str(candidate or "")).strip()
        if cleaned and cleaned not in queue:
            queue.append(cleaned)
        if len(queue) >= LOOKUP_MAX_ITERATIONS:
            break
    return queue[:LOOKUP_MAX_ITERATIONS]


def normalize_lookup_text(text: str) -> str:
    normalized = str(text or "")
    for mark in [". ", "! ", "? ", "; ", ": "]:
        normalized = normalized.replace(mark, mark.strip() + "\n")
    normalized = normalized.replace(")", ")\n")
    normalized = normalized.replace(" шт.", " шт.\n")
    return normalized


def _looks_like_followup_query(text: str) -> bool:
    lower = str(text or "").strip().lower()
    if not lower:
        return False
    followup_markers = [
        "это", "этот", "эта", "эти", "там", "тут", "они", "она", "он", "их", "ним", "ними",
        "из них", "по ним", "по этому", "то же", "также", "а еще", "а какие", "а сколько", "тогда",
    ]
    if any(marker in lower for marker in followup_markers):
        return True
    # Very short factual follow-ups usually depend on the previous turn.
    words = [part for part in lower.replace("?", " ").split() if part]
    return len(words) <= 3 and not any(token in lower for token in ["бакалавр", "магистр", "доктор", "общежит", "документ", "программ", "балл"])


def lookup_basis(state: AgentState, *, recent_history: Callable[[dict[str, Any], int], str]) -> str:
    latest = state["user_message"].strip()
    history = recent_history(state["session"], limit=6)
    # Do not pollute retrieval with previous unrelated questions. History is used only for elliptical follow-ups.
    if history and _looks_like_followup_query(latest):
        return f"{history}\nlatest user message: {latest}"
    return latest


def query_stems(query: str, *, simple_words: Callable[[str], list[str]]) -> set[str]:
    return {word[:5] for word in simple_words(query) if len(word) >= 4}


def text_structure_bonus(
    query: str,
    text: str,
    *,
    contains_numbered_list: Callable[[str], bool],
    looks_like_phone: Callable[[str], bool],
    has_url_or_email: Callable[[str], bool],
    contains_large_number: Callable[[str], bool],
    contains_year: Callable[[str], bool],
) -> int:
    query_lower = query.lower()
    text_lower = text.lower()
    bonus = 0
    if any(token in query_lower for token in ["документ", "документы", "перечень", "справка"]):
        if "перечень документов" in text_lower:
            bonus += 8
        if contains_numbered_list(text_lower):
            bonus += 4
        if any(token in text_lower for token in ["заявление", "копия", "электронная копия", "документ, удостоверяющий личность"]):
            bonus += 4
    if any(token in query_lower for token in ["контакт", "телефон", "почта", "email", "e-mail", "где находится"]):
        if looks_like_phone(text):
            bonus += 5
        if has_url_or_email(text):
            bonus += 4
        if any(token in text_lower for token in ["телефон", "контакт", "почта", "адрес", "email", "e-mail"]):
            bonus += 4
    if any(token in query_lower for token in ["цена", "стоимость", "сколько", "оплата"]):
        if any(token in text_lower for token in ["тенге", "стоим", "оплата", "тг", "курс", "сумма"]):
            bonus += 6
        if contains_large_number(text):
            bonus += 2
    if any(token in query_lower for token in ["срок", "дата", "когда", "этапы"]):
        if contains_year(text) or any(month in text_lower for month in ["январ", "феврал", "март", "апрел", "май", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]):
            bonus += 6
    if any(token in query_lower for token in ["льгот", "скидк", "пособ", "грант"]):
        if any(token in text_lower for token in ["льгот", "скид", "социаль", "положение о предоставлении"]):
            bonus += 5
    return bonus


def path_bonus(query: str, path_to_file: str, *, query_stems: Callable[[str], set[str]], simple_words: Callable[[str], list[str]]) -> int:
    query_stem_set = query_stems(query)
    path_words = {word[:5] for word in simple_words(path_to_file) if len(word) >= 4}
    return sum(1 for stem in query_stem_set if stem in path_words)


def _payload_domain_candidates(payload: dict[str, Any]) -> set[str]:
    metadata = dict(payload.get("metadata", {}) or {})
    candidates = {
        str(payload.get("domain") or "").lower(),
        str(payload.get("class_name") or "").lower(),
        str(metadata.get("domain") or "").lower(),
        str(metadata.get("class_name") or "").lower(),
    }
    source_file = str(payload.get("source_file") or payload.get("path_to_file") or "").replace("\\", "/").lower()
    for domain_name in [
        "benefits",
        "contacts",
        "documents",
        "housing",
        "master",
        "programs",
        "scores",
        "timeline",
        "tuition",
        "university_info",
    ]:
        if f"/{domain_name}/" in f"/{source_file}" or source_file.startswith(f"{domain_name}/"):
            candidates.add(domain_name)
    return {item for item in candidates if item}


def payload_matches_filters(payload: dict[str, Any], filters: dict[str, Any]) -> bool:
    domain_candidates = _payload_domain_candidates(payload)
    schema = str(payload.get("schema") or "").lower()
    education_level = str(payload.get("education_level") or "").lower()
    language = str(payload.get("language") or "").lower()
    selected_domains = [str(item).lower() for item in filters.get("domains", []) if item]
    selected_schemas = [str(item).lower() for item in filters.get("schemas", []) if item]
    if selected_domains and not any(domain in domain_candidates for domain in selected_domains):
        return False
    if selected_schemas and schema not in selected_schemas:
        return False
    if filters.get("education_level"):
        # For program/program-score queries, missing level is not neutral: it often lets master/phd or generic text leak into bachelor retrieval.
        if selected_domains and any(domain_name in selected_domains for domain_name in ["programs", "scores"]):
            if education_level != filters["education_level"]:
                return False
        elif education_level and education_level != filters["education_level"]:
            return False
    if filters.get("language") and language and language != filters["language"]:
        return False
    return True



def _scroll_payloads(limit: int = 2000) -> list[dict[str, Any]]:
    client = get_index().client
    collection_name = get_index().collection_name
    payloads: list[dict[str, Any]] = []
    offset = None
    while len(payloads) < limit:
        points, offset = client.scroll(
            collection_name=collection_name,
            limit=min(256, limit - len(payloads)),
            with_payload=True,
            with_vectors=False,
            offset=offset,
        )
        for point in points:
            payloads.append(point.payload or {})
            if len(payloads) >= limit:
                break
        if offset is None:
            break
    return payloads


def _query_subject_aliases(query: str) -> list[list[str]]:
    lower = str(query or "").lower()
    aliases: list[list[str]] = []
    if "математ" in lower:
        aliases.append(["математ"])
    if "информат" in lower or "программ" in lower or "алгоритм" in lower:
        aliases.append(["информат", "основы алгоритм", "алгоритмиз", "программирован", "программное обеспечение"])
    if "физик" in lower:
        aliases.append(["физик"])
    if "географ" in lower:
        aliases.append(["географ"])
    if "хими" in lower:
        aliases.append(["хими"])
    if "биолог" in lower:
        aliases.append(["биолог"])
    return aliases


def _payload_text_blob(payload: dict[str, Any]) -> str:
    metadata = dict(payload.get("metadata", {}) or {})
    fields = dict(metadata.get("fields", {}) or {})
    system_fields = dict(metadata.get("system_fields", {}) or {})
    parts = [
        payload.get("title", ""),
        payload.get("text", ""),
        payload.get("raw_text", ""),
        " ".join(str(value) for value in fields.values() if value not in (None, "")),
        " ".join(str(value) for value in system_fields.values() if value not in (None, "")),
    ]
    return "\n".join(str(part) for part in parts if part).lower()


def _matches_subject_aliases(payload: dict[str, Any], aliases: list[list[str]]) -> bool:
    if not aliases:
        return False
    blob = _payload_text_blob(payload)
    return all(any(alias in blob for alias in alias_group) for alias_group in aliases)


def _program_subject_context(query: str, *, payload_context_snippet: Callable[[dict[str, Any]], str]) -> list[str]:
    lower = str(query or "").lower()
    wants_programs = any(token in lower for token in ["программ", "специальност", "образовательн", "направлен"])
    wants_subjects = any(token in lower for token in ["предмет", "математ", "информат", "физик", "профиль"])
    if not (wants_programs and wants_subjects):
        return []
    aliases = _query_subject_aliases(query)
    if not aliases:
        return []
    require_bachelor = any(token in lower for token in ["бакалавр", "вуз", "ент", "ұбт", "после школы"])
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for payload in _scroll_payloads(limit=3000):
        domain = str(payload.get("domain") or payload.get("class_name") or "").lower()
        if domain != "programs":
            continue
        education_level = str(payload.get("education_level") or "").lower()
        if require_bachelor and education_level != "bachelor":
            continue
        if not _matches_subject_aliases(payload, aliases):
            continue
        text = payload_context_snippet(payload).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        blob = _payload_text_blob(payload)
        score = 0
        if str(payload.get("schema") or "").lower() == "program_entry":
            score += 8
        if education_level == "bachelor":
            score += 6
        if "профиль" in blob or "предмет 1" in blob or "предмет 2" in blob:
            score += 4
        if "очно-сокращ" in blob or "сокращенное" in blob:
            score -= 1
        scored.append((score, text))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in scored[:8]]


def _program_overview_context(query: str, *, payload_context_snippet: Callable[[dict[str, Any]], str]) -> list[str]:
    lower = str(query or "").lower()
    wants_programs = any(
        token in lower
        for token in ["программ", "специальност", "образовательн", "направлен", "мамандық", "бағдарлама"]
    )
    precise_fact = any(
        token in lower
        for token in [
            "балл",
            "порог",
            "стоим",
            "цена",
            "оплата",
            "документ",
            "перечень",
            "срок",
            "дата",
            "общежит",
        ]
    )
    if not wants_programs or precise_fact:
        return []

    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for payload in _scroll_payloads(limit=3000):
        domain_candidates = _payload_domain_candidates(payload)
        text_blob = _payload_text_blob(payload)
        source_file = str(payload.get("source_file") or payload.get("path_to_file") or "").lower()
        if "programs" not in domain_candidates and "образовательн" not in text_blob and "білім беру" not in text_blob:
            continue

        score = 0
        schema = str(payload.get("schema") or "").lower()
        title = str(payload.get("title") or "").lower()
        if "programs" in domain_candidates:
            score += 8
        if schema in {"program_entry", "table_facts"}:
            score += 4
        if any(
            token in title
            for token in [
                "образовательные программы",
                "приложение",
                "бакалавриат",
                "магистратура",
                "докторантура",
            ]
        ):
            score += 4
        if any(
            token in text_blob
            for token in [
                "группа образовательных программ",
                "образовательная программа",
                "наименование образовательной программы",
                "код и классификация",
            ]
        ):
            score += 5
        if "серпін" in text_blob or "serpin" in source_file:
            score -= 3
        if score <= 0:
            continue
        text = payload_context_snippet(payload).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        scored.append((score, text))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _, text in scored[:10]]


def _local_retrieval_filters(query: str) -> dict[str, Any] | None:
    lower = str(query or "").lower()
    domains: list[str] = []
    education_level = None
    words = set(lower.replace("?", " ").replace(",", " ").replace(".", " ").split())
    if "бакалавр" in lower or "после школы" in lower or "ұбт" in words or "ент" in words:
        education_level = "bachelor"
    elif any(token in lower for token in ["магистр", "магистрат"]):
        education_level = "master"
    elif any(token in lower for token in ["докторан", "phd", "доктор"]):
        education_level = "phd"

    if any(token in lower for token in ["программ", "специальност", "образовательн", "профильн", "предмет"]):
        domains.append("programs")
    if any(token in lower for token in ["балл", "порог", "грант", "квот"]):
        for domain in ["scores", "programs"]:
            if domain not in domains:
                domains.append(domain)
    if any(token in lower for token in ["документ", "перечень", "копия", "справка"]):
        domains.append("documents")
    if any(token in lower for token in ["общежит", "заселен", "проживан", "договор найма"]):
        domains = ["housing"]
    if any(token in lower for token in ["стоим", "цена", "оплата", "тенге"]):
        domains = ["tuition"]
    if any(token in lower for token in ["срок", "дата", "когда", "календар"]):
        domains = ["timeline"]
    if not domains and education_level is None:
        return None
    # Do not restrict schemas here: imported Excel rows are often generic_text even when they are program rows.
    return {"domains": domains or list(DEFAULT_RETRIEVAL_DOMAINS), "schemas": [], "education_level": education_level, "language": None}

def retrieve_knowledge_context(
    query: str,
    history_text: str = "",
    *,
    llm_retrieval_filters: Callable[[str, str], dict[str, Any]],
    direct_contact_context: Callable[[str], list[str]],
    query_stems: Callable[[str], set[str]],
    path_bonus: Callable[[str, str], int],
    text_structure_bonus: Callable[[str, str], int],
    payload_context_snippet: Callable[[dict[str, Any]], str],
    payload_matches_filters: Callable[[dict[str, Any], dict[str, Any]], bool],
    generate_lookup_queries: Callable[[str], list[str]],
) -> list[str]:
    direct_ctx = direct_contact_context(query)
    if direct_ctx:
        return direct_ctx
    program_ctx = _program_subject_context(query, payload_context_snippet=payload_context_snippet)
    if program_ctx:
        return program_ctx
    program_overview_ctx = _program_overview_context(query, payload_context_snippet=payload_context_snippet)
    if program_overview_ctx:
        return program_overview_ctx
    retrieval_filters = _local_retrieval_filters(query)
    if retrieval_filters is None:
        if query.strip():
            try:
                retrieval_filters = llm_retrieval_filters(query, history_text)
            except Exception:
                retrieval_filters = {"domains": list(DEFAULT_RETRIEVAL_DOMAINS), "schemas": [], "education_level": None, "language": None}
        else:
            retrieval_filters = {"domains": list(DEFAULT_RETRIEVAL_DOMAINS), "schemas": [], "education_level": None, "language": None}
    if not retrieval_filters.get("domains"):
        retrieval_filters["domains"] = ["university_info"]
    stems = query_stems(query)
    for threshold in (0.8, 0.65, 0.5):
        ctx = find_passage(
            query,
            get_index(),
            threshold=threshold,
            domains=retrieval_filters.get("domains"),
            schemas=retrieval_filters.get("schemas"),
            education_level=retrieval_filters.get("education_level"),
            language=retrieval_filters.get("language"),
        )
        if ctx is None:
            continue
        ctx_list = [ctx] if isinstance(ctx, str) else ctx
        if ctx_list:
            head = ctx_list[0].lower()
            overlap = sum(1 for stem in stems if stem and stem in head)
            if overlap > 0:
                return ctx_list
    if not stems:
        return []
    scored: list[tuple[int, str]] = []
    for payload in _scroll_payloads(limit=3000):
        text = str(payload.get("text") or "")
        path_to_file = str(payload.get("path_to_file") or payload.get("source_file") or "").lower()
        if not text or not payload_matches_filters(payload, retrieval_filters):
            continue
        haystack = text.lower()
        score = sum(1 for stem in stems if stem and stem in haystack)
        score += path_bonus(query, path_to_file)
        score += text_structure_bonus(query, text)
        if score > 0:
            scored.append((score, payload_context_snippet(payload)))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        return [text for _, text in scored[:3]]
    for alt_query in generate_lookup_queries(query):
        for threshold in (0.8, 0.65, 0.5):
            ctx = find_passage(
                alt_query,
                get_index(),
                threshold=threshold,
                domains=retrieval_filters.get("domains"),
                schemas=retrieval_filters.get("schemas"),
                education_level=retrieval_filters.get("education_level"),
                language=retrieval_filters.get("language"),
            )
            if ctx is None:
                continue
            ctx_list = [ctx] if isinstance(ctx, str) else ctx
            if ctx_list:
                return ctx_list
    return []


def targeted_document_context(
    query: str,
    *,
    text_structure_bonus: Callable[[str, str], int],
    path_bonus: Callable[[str, str], int],
    payload_context_snippet: Callable[[dict[str, Any]], str],
    contains_numbered_list: Callable[[str], bool],
) -> list[str]:
    query_lower = query.lower()
    if not any(token in query_lower for token in ["документ", "документы", "перечень", "справка"]):
        return []
    scored: list[tuple[int, str]] = []
    for payload in _scroll_payloads(limit=3000):
        text = str(payload.get("text") or "")
        path_to_file = str(payload.get("path_to_file") or "").lower()
        if not text:
            continue
        lowered = text.lower()
        score = text_structure_bonus(query, text)
        score += path_bonus(query, path_to_file)
        if "перечень документов" in lowered:
            score += 8
        if any(token in lowered for token in ["заявление", "договор найма", "акт приема-передачи", "анкета проживающего", "копия", "аттестат", "диплом", "паспорт", "фотокарточ", "миграцион"]):
            score += 5
        if contains_numbered_list(text):
            score += 4
        if any(token in lowered for token in ["личность", "электронная копия", "удостоверение личности"]):
            score += 3
        if score > 0:
            scored.append((score, payload_context_snippet(payload)))
    scored.sort(key=lambda item: item[0], reverse=True)
    picked: list[str] = []
    seen = set()
    for _score, candidate_text in scored:
        if candidate_text in seen:
            continue
        seen.add(candidate_text)
        picked.append(candidate_text)
        if len(picked) >= 4:
            break
    return picked


def extract_address_answer(
    query: str,
    ctx_list: list[str],
    lang: str,
    *,
    normalize_spaces: Callable[[Any], str],
    normalize_lookup_text: Callable[[str], str],
) -> str | None:
    query_lower = query.lower()
    if not any(token in query_lower for token in ["адрес", "где находится", "местонахождение", "контакт"]):
        return None
    merged = normalize_lookup_text("\n".join(ctx_list))
    address = ""
    for line in merged.splitlines():
        candidate = normalize_spaces(line).strip(" .;")
        lowered = candidate.lower()
        if not candidate:
            continue
        if "караганд" in lowered and any(marker in lowered for marker in ["пр.", "просп", "улиц", "адрес", "телефон"]):
            address = candidate
            break
        if lowered.startswith("адрес") and ":" in candidate:
            address = candidate.split(":", 1)[1].strip(" .;")
            break
    if not address:
        return None
    if lang == "kk":
        return f"Қолжетімді материалдарда мына мекенжай көрсетілген: {address}."
    if lang == "en":
        return f"The available university materials mention this address: {address}."
    return f"В доступных материалах указан такой адрес: {address}."


def extract_document_answer(
    query: str,
    ctx_list: list[str],
    lang: str,
    *,
    targeted_document_context: Callable[[str], list[str]],
    normalize_lookup_text: Callable[[str], str],
    normalize_spaces: Callable[[Any], str],
    strip_leading_list_marker: Callable[[str], str],
) -> str | None:
    query_lower = query.lower()
    if not any(token in query_lower for token in ["документ", "документы", "перечень", "справка", "копия"]):
        return None
    combined_ctx = [*ctx_list]
    for extra in targeted_document_context(query):
        if extra not in combined_ctx:
            combined_ctx.append(extra)
    items: list[str] = []
    for block in combined_ctx:
        for line in normalize_lookup_text(block).splitlines():
            cleaned = normalize_spaces(strip_leading_list_marker(line)).strip(" .;\t")
            lowered = cleaned.lower()
            if not cleaned or len(cleaned) < 8:
                continue
            if any(marker in lowered for marker in ["перечень документов", "основания для отказа", "максимально допустимое время", "результат оказания", "номер:", "действие "]):
                continue
            if any(marker in lowered for marker in ["document:", "source file:", "источник", "тип документа", "назначение"]):
                continue
            if any(token in lowered for token in ["заявление", "копия", "электронная копия", "документ, удостоверяющий личность", "договор найма", "акт приема-передачи", "анкета проживающего", "кандас", "аттестат", "диплом", "паспорт", "фотокарточ", "миграцион", "регистрац"]):
                items.append(cleaned)
    deduped: list[str] = []
    seen = set()
    for item in items:
        normalized = normalize_spaces(item.lower()).strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item)
        if len(deduped) >= 25:
            break
    if not deduped:
        return None
    if "общежит" in query_lower or "проживан" in query_lower or "заселен" in query_lower:
        if lang == "kk":
            intro = "Қолжетімді материалдар бойынша жатақханаға орын алу үшін мына құжаттар көрсетілген:"
            outro = "Ескерту: кейбір растайтын құжаттар өтініш берушінің мәртебесіне байланысты."
        elif lang == "en":
            intro = "According to the available materials, these documents are listed for obtaining a dormitory place:"
            outro = "Note: some supporting documents depend on the applicant's status."
        else:
            intro = "По доступным материалам для получения места в общежитии указаны следующие документы:"
            outro = "Примечание: часть подтверждающих документов зависит от статуса заявителя."
    else:
        if lang == "kk":
            intro = "Қолжетімді материалдарда мына құжаттар көрсетілген:"
            outro = ""
        elif lang == "en":
            intro = "The available materials list the following documents:"
            outro = ""
        else:
            intro = "По доступным материалам нужны следующие документы:"
            outro = ""
    bullets = "\n".join(f"- {item}" for item in deduped)
    return f"{intro}\n{bullets}" + (f"\n{outro}" if outro else "")
