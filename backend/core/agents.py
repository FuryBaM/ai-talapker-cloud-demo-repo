import json
import re
from typing import Any, Dict, List

from core.agent_state import AgentState
from core.calculator import CalculatorError, calculate, looks_like_calculation
from core.config import ANSWER_NOT_FOUND, DEFAULT_RETRIEVAL_DOMAINS, LOOKUP_MAX_ITERATIONS
from core.generation import generate_answer, generate_from_messages
from core.general_service import (
    assistant_identity_reply as service_assistant_identity_reply,
    assistant_location_reply as service_assistant_location_reply,
    assistant_name_reply as service_assistant_name_reply,
    detect_meta_intent as service_detect_meta_intent,
    general_reply as service_general_reply,
    is_greeting as service_is_greeting,
    llm_turn_kind as service_llm_turn_kind,
    looks_like_interview_reply as service_looks_like_interview_reply,
)
from core.interview_generation_service import (
    alternate_interview_prompt as service_alternate_interview_prompt,
    generate_interview_followup as service_generate_interview_followup,
)
from core.interview_service import (
    field_anchor as interview_field_anchor,
    field_prompt as interview_field_prompt,
    field_question_seed as interview_field_question_seed,
    next_interview_question as interview_next_question,
    next_missing_field as interview_next_missing_field,
    profile_is_complete as interview_profile_is_complete,
)
from core.knowledge_assets import get_index, get_programs
from core.lookup_service import (
    build_lookup_query_queue as service_build_lookup_query_queue,
    direct_contact_context as service_direct_contact_context,
    extract_address_answer as service_extract_address_answer,
    extract_document_answer as service_extract_document_answer,
    generate_lookup_queries as service_generate_lookup_queries,
    is_contact_query as service_is_contact_query,
    lookup_basis as service_lookup_basis,
    normalize_lookup_text as service_normalize_lookup_text,
    path_bonus as service_path_bonus,
    payload_matches_filters as service_payload_matches_filters,
    query_stems as service_query_stems,
    retrieve_knowledge_context as service_retrieve_knowledge_context,
    safe_university_fallback as service_safe_university_fallback,
    targeted_document_context as service_targeted_document_context,
    text_structure_bonus as service_text_structure_bonus,
)
from core.rag import find_passage, search_debug
from core.recommendation_service import (
    build_recommendations as service_build_recommendations,
    missing_profile_fields as service_missing_profile_fields,
    profile_text as service_profile_text,
)
from core.retrieval_planner import (
    RAG_DOMAINS,
    llm_preferred_domains as planner_llm_preferred_domains,
    llm_retrieval_filters as planner_llm_retrieval_filters,
    payload_context_snippet as planner_payload_context_snippet,
)
from core.text_utils import (
    contains_large_number as text_contains_large_number,
    contains_numbered_list as text_contains_numbered_list,
    contains_year as text_contains_year,
    cyrillic_word_count as text_cyrillic_word_count,
    digit_tokens as text_digit_tokens,
    extract_json_object as text_extract_json_object,
    extract_json_text as text_extract_json_text,
    extract_one_of as text_extract_one_of,
    has_url_or_email as text_has_url_or_email,
    latin_word_count as text_latin_word_count,
    looks_like_phone as text_looks_like_phone,
    normalize_spaces as text_normalize_spaces,
    simple_words as text_simple_words,
    strip_leading_list_marker as text_strip_leading_list_marker,
)
from core.web_lookup import search_whitelisted_web
from core.conversation_memory import append_raw_message, recent_raw_history


ROUTE_NAMES = {"general", "interview", "knowledge", "career", "scoring", "recommendation", "calculator", "lookup"}
INTERVIEW_FLOW_MARKER = "__interview__"
UNIVERSITY_NAME_RU = "Карагандинский технический университет имени Абылкаса Сагинова"
UNIVERSITY_NAME_EN = "Karaganda Technical University named after Abylkas Saginov"
UNIVERSITY_CITY_RU = "Караганда"
UNIVERSITY_COUNTRY_RU = "Казахстан"
UNIVERSITY_LOCATION_RU = f"{UNIVERSITY_CITY_RU}, {UNIVERSITY_COUNTRY_RU}"

def reload_knowledge_assets(*args, **kwargs):
    from core.knowledge_assets import reload_knowledge_assets as _reload
    return _reload(*args, **kwargs)


def shutdown_knowledge_assets() -> None:
    from core.knowledge_assets import shutdown_knowledge_assets as _shutdown
    _shutdown()


def _message_lower(state: AgentState) -> str:
    return state["user_message"].lower()


def _profile_is_complete(profile: Dict[str, Any]) -> bool:
    return interview_profile_is_complete(profile)


def _recent_history(session: Dict[str, Any], limit: int = 8) -> str:
    return recent_raw_history(session, limit=limit)


def _recent_facts(session: Dict[str, Any], limit: int = 12) -> str:
    facts = session.get("facts", [])[-limit:]
    lines = []
    for fact in facts:
        text = str(fact.get("text") or "").strip()
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)




def _profile_memory_lines(profile: Dict[str, Any]) -> List[str]:
    """Compact user profile facts used as conversational memory.

    This is applicant/session context, not official university context.
    It helps resolve later turns like "what suits me", "what about grants",
    or follow-ups that depend on earlier messages.
    """
    lines: List[str] = []
    if not isinstance(profile, dict):
        return lines
    if profile.get("user_name"):
        lines.append(f"name: {profile['user_name']}")
    if profile.get("age") is not None:
        lines.append(f"age: {profile['age']}")
    if profile.get("favorite_subjects"):
        subjects = profile.get("favorite_subjects")
        if isinstance(subjects, list):
            joined = ", ".join(str(item) for item in subjects if item)
        else:
            joined = str(subjects)
        if joined:
            lines.append(f"profile subjects: {joined}")
    if profile.get("ent_score") is not None:
        lines.append(f"ENT score: {profile['ent_score']}")
    if profile.get("career_goal"):
        lines.append(f"target field: {profile['career_goal']}")
    if profile.get("activity_type"):
        lines.append(f"preferred activity type: {profile['activity_type']}")
    if profile.get("interests"):
        interests = profile.get("interests")
        if isinstance(interests, list):
            joined = ", ".join(str(item) for item in interests if item)
        else:
            joined = str(interests)
        if joined:
            lines.append(f"interests: {joined}")
    if profile.get("language"):
        lines.append(f"study language: {profile['language']}")
    if profile.get("budget"):
        lines.append(f"budget/payment preference: {profile['budget']}")
    return lines


def _memory_context(state: AgentState, *, include_history: bool = False) -> str:
    session = state.get("session", {})
    profile = state.get("profile") or session.get("profile", {})
    lines: List[str] = []
    prompt_memory = str(state.get("memory_prompt") or "").strip()
    if prompt_memory:
        lines.append(prompt_memory)
    else:
        profile_lines = _profile_memory_lines(profile)
        if profile_lines:
            lines.append("User profile:\n" + "\n".join(profile_lines))
    fact_text = _recent_facts(session, limit=10)
    if fact_text:
        lines.append("Known facts:\n" + fact_text)
    if include_history:
        history = _recent_history(session, limit=12)
        if history and "Recent raw messages:" not in prompt_memory:
            lines.append("Recent raw messages:\n" + history)
    return "\n\n".join(lines).strip()


def _latest_needs_memory(text: str) -> bool:
    lower = str(text or "").lower()
    if not lower:
        return False
    markers = [
        "мне", "мой", "моя", "мои", "меня", "для меня", "подходит", "подойдут",
        "посоветуй", "подбери", "рекоменд", "какую программу", "какие программы",
        "какая специальность", "какие специальности", "с моими", "по моим", "мой балл",
        "ент", "балл", "профильные предметы", "математика", "информатика",
        "i ", "me", "my", "for me", "recommend", "suit", "fit",
    ]
    if any(marker in lower for marker in markers):
        return True
    short_words = [part for part in lower.replace("?", " ").split() if part]
    return len(short_words) <= 5 and any(token in lower for token in ["а", "тогда", "ещё", "еще", "что", "какие"])


def _memory_augmented_lookup_query(state: AgentState, query: str) -> str:
    base = str(query or "").strip()
    if not base:
        base = state.get("user_message", "").strip()
    if not _latest_needs_memory(state.get("user_message", "")):
        return base
    memory = _memory_context(state)
    if not memory:
        return base
    return (
        f"{base}\n\n"
        "Applicant/session memory for resolving this query, not an official source:\n"
        f"{memory}"
    )
def _lang_label(lang: str) -> str:
    if lang == "en":
        return "English"
    if lang == "kk":
        return "Kazakh"
    return "Russian"


def _localized(lang: str, ru: str, en: str, kk: str | None = None) -> str:
    if lang == "en":
        return en
    if lang == "kk" and kk is not None:
        return kk
    return ru


def _normalize_spaces(text: Any) -> str:
    return text_normalize_spaces(text)


def _extract_json_text(raw: str) -> str:
    return text_extract_json_text(raw)


def _extract_one_of(text: str, options: List[str], default: str | None = None) -> str | None:
    return text_extract_one_of(text, options) or default


def _simple_words(text: str) -> List[str]:
    return text_simple_words(text)


def _digit_tokens(text: str) -> List[str]:
    return text_digit_tokens(text)


def _contains_year(text: str) -> bool:
    return text_contains_year(text)


def _contains_large_number(text: str, min_digits: int = 3) -> bool:
    return text_contains_large_number(text, min_digits=min_digits)


def _looks_like_phone(text: str) -> bool:
    return text_looks_like_phone(text)


def _has_url_or_email(text: str) -> bool:
    return text_has_url_or_email(text)


def _contains_numbered_list(text: str) -> bool:
    for raw_line in str(text or "").splitlines():
        line = raw_line.lstrip()
        if not line:
            continue
        if line.startswith(("-", "•", "*")):
            return True
        digits = []
        for char in line:
            if char.isdigit():
                digits.append(char)
                continue
            break
        if digits:
            tail = line[len(digits) :].lstrip()
            if tail.startswith((")", ".")):
                return True
    return False


def _strip_leading_list_marker(text: str) -> str:
    line = str(text or "").lstrip()
    while line and line[0] in "-*•":
        line = line[1:].lstrip()
    digits = []
    for char in line:
        if char.isdigit():
            digits.append(char)
            continue
        break
    if digits:
        rest = line[len(digits) :].lstrip()
        if rest.startswith((")", ".")):
            line = rest[1:].lstrip()
    return line.strip()


def _cyrillic_word_count(text: str) -> int:
    return sum(1 for token in _simple_words(text) if any("а" <= ch <= "я" or ch in "ёәіңғүұқөһ" for ch in token))


def _latin_word_count(text: str) -> int:
    return text_latin_word_count(text)


def _llm_preferred_domains(query: str, history_text: str = "") -> List[str]:
    return planner_llm_preferred_domains(query, history_text)


def _llm_retrieval_filters(query: str, history_text: str = "") -> Dict[str, Any]:
    return planner_llm_retrieval_filters(query, history_text)


def _payload_context_snippet(payload: Dict[str, Any]) -> str:
    return planner_payload_context_snippet(payload)


def _field_prompt(field: str, lang: str) -> str:
    return interview_field_prompt(field, lang)


def _field_anchor(field: str) -> Dict[str, str]:
    return interview_field_anchor(field)


def _field_question_seed(field: str, lang: str) -> str:
    return interview_field_question_seed(field, lang)


def _interview_program_candidates(profile: Dict[str, Any], user_message: str, limit: int = 6) -> List[str]:
    try:
        _scoring, recommendations = _build_recommendations(profile, user_message)
    except Exception:
        recommendations = []
    names: List[str] = []
    for item in recommendations:
        program = str(item.get("program") or "").strip()
        if program and program not in names:
            names.append(program)
        if len(names) >= limit:
            break
    if names:
        return names
    fallback_names: List[str] = []
    for program in get_programs()[:limit]:
        if program.name not in fallback_names:
            fallback_names.append(program.name)
    return fallback_names


def _generate_interview_choices(state: AgentState, profile: Dict[str, Any], field: str, anchor_context: List[str]) -> List[str]:
    if not state.get("use_llm", True):
        return []
    field_meta = _field_anchor(field)
    program_candidates = _interview_program_candidates(profile, state["user_message"], limit=6)
    messages = [
        {
            "role": "system",
            "content": (
                "Generate 3 to 5 short concrete choice options for an applicant interview. "
                "Ground them only in the provided university context and candidate program names. "
                "Return one option per line, without numbering or explanation. "
                "Options may be directions, study formats, or concrete program examples, depending on the missing field."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Language: {_lang_label(state['lang'])}\n"
                f"Missing field: {field}\n"
                f"Field anchor: {field_meta.get('anchor', field)}\n"
                f"Field goal: {field_meta.get(state['lang'], field_meta.get('ru', field))}\n\n"
                f"Known profile: {profile}\n\n"
                f"Knowledge snippets:\n{chr(10).join(anchor_context) or '-'}\n\n"
                f"Candidate programs:\n{chr(10).join(program_candidates) or '-'}"
            ),
        },
    ]
    raw = generate_from_messages(messages, max_new_tokens=120, ctx_texts=None).strip()
    options: List[str] = []
    for line in raw.splitlines():
        option = _strip_leading_list_marker(line).strip()
        if option and option not in options:
            options.append(option)
        if len(options) >= 5:
            break
    return options


def _next_missing_field(profile: Dict[str, Any]) -> tuple[str, str] | None:
    return interview_next_missing_field(profile)


def _next_interview_question(profile: Dict[str, Any]) -> str | None:
    return interview_next_question(profile)


def _extract_json_object(text: str) -> Dict[str, Any]:
    return text_extract_json_object(text)


def _assistant_name_reply(lang: str) -> str:
    return service_assistant_name_reply(lang, UNIVERSITY_NAME_RU, UNIVERSITY_NAME_EN)

def _assistant_identity_reply(lang: str) -> str:
    return service_assistant_identity_reply(lang, UNIVERSITY_NAME_RU, UNIVERSITY_NAME_EN, UNIVERSITY_CITY_RU, UNIVERSITY_COUNTRY_RU)

def _assistant_location_reply(lang: str) -> str:
    return service_assistant_location_reply(lang, UNIVERSITY_NAME_RU, UNIVERSITY_CITY_RU, UNIVERSITY_COUNTRY_RU)

def _detect_meta_intent(state: AgentState) -> str:
    return service_detect_meta_intent(
        state,
        llm_turn_kind_fn=_llm_turn_kind,
        recent_history=_recent_history,
        recent_facts=_recent_facts,
        extract_one_of=_extract_one_of,
    )

def _llm_turn_kind(state: AgentState) -> str:
    return service_llm_turn_kind(
        state,
        recent_history=_recent_history,
        recent_facts=_recent_facts,
        extract_one_of=_extract_one_of,
    )

def _is_greeting(text: str) -> bool:
    return service_is_greeting(text)

def _looks_like_interview_reply(text: str) -> bool:
    return service_looks_like_interview_reply(text)

def _normalize_profile_patch(data: Dict[str, Any], current_profile: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {**current_profile}
    if not data:
        return normalized

    user_name = data.get("user_name")
    if isinstance(user_name, str) and user_name.strip():
        normalized["user_name"] = user_name.strip().title()

    age = data.get("age")
    if isinstance(age, int) and 12 <= age <= 99:
        normalized["age"] = age
    elif isinstance(age, str) and age.isdigit():
        age_int = int(age)
        if 12 <= age_int <= 99:
            normalized["age"] = age_int

    favorite_subjects = data.get("favorite_subjects")
    if isinstance(favorite_subjects, list):
        normalized["favorite_subjects"] = [str(item).strip().lower() for item in favorite_subjects if str(item).strip()]

    activity_type = data.get("activity_type")
    if isinstance(activity_type, str) and activity_type.strip():
        normalized["activity_type"] = activity_type.strip().lower()

    interests = data.get("interests")
    if isinstance(interests, list):
        normalized["interests"] = [str(item).strip().lower() for item in interests if str(item).strip()]

    career_goal = data.get("career_goal")
    if isinstance(career_goal, str) and career_goal.strip():
        normalized["career_goal"] = career_goal.strip()

    ent_score = data.get("ent_score")
    if isinstance(ent_score, int) and 0 <= ent_score <= 140:
        normalized["ent_score"] = ent_score
    elif isinstance(ent_score, str) and ent_score.isdigit():
        score_int = int(ent_score)
        if 0 <= score_int <= 140:
            normalized["ent_score"] = score_int

    language = data.get("language")
    if isinstance(language, str) and language.strip():
        normalized["language"] = language.strip().lower()

    budget = data.get("budget")
    if isinstance(budget, str) and budget.strip():
        normalized["budget"] = budget.strip().lower()

    return normalized


def _normalize_fact_items(items: Any) -> List[str]:
    if not isinstance(items, list):
        return []
    normalized: List[str] = []
    seen = set()
    for item in items:
        text = _normalize_spaces(str(item or "")).strip(" -•\t\n")
        key = text.lower()
        if len(text) < 3 or key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized[:12]


def _llm_extract_memory_packet(state: AgentState, current_profile: Dict[str, Any]) -> tuple[Dict[str, Any], List[str]]:
    messages = [
        {
            "role": "system",
            "content": (
                "Extract explicit applicant memory from the latest user message and short conversation history. "
                "Return only valid JSON with this shape: "
                "{\"profile_patch\": {\"user_name\": string|null, \"age\": integer|null, "
                "\"favorite_subjects\": string[]|null, \"activity_type\": string|null, "
                "\"career_goal\": string|null, \"ent_score\": integer|null, "
                "\"language\": string|null, \"budget\": string|null, \"interests\": string[]|null}, "
                "\"facts\": string[]}. "
                "Only include facts about the user stated in the conversation. "
                "Do not invent facts. Do not include facts about the assistant. "
                "This extraction step is the source of truth for user memory. "
                "Do not assume any regex parser or manual field extractor exists. "
                "Decide the profile patch and fact list yourself from the latest message, recent history, known facts, and current profile. "
                "If the latest user reply is short but clearly describes a direction, field, specialization, or professional interest, "
                "use it to fill career_goal and/or interests. "
                "Examples of terse but valid user facts include replies like 'programming', 'game development', "
                "'artificial intelligence', 'cybersecurity', 'data analytics', or similar domain phrases."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Conversation history:\n{_recent_history(state['session'])}\n\n"
                f"Known facts:\n{_recent_facts(state['session']) or '-'}\n\n"
                f"Current profile:\n{current_profile}\n\n"
                f"Latest user message:\n{state['user_message']}"
            ),
        },
    ]
    raw = generate_from_messages(messages, max_new_tokens=220, ctx_texts=None)
    packet = _extract_json_object(raw)
    if not packet:
        return {}, []
    profile_patch = packet.get("profile_patch")
    if not isinstance(profile_patch, dict):
        profile_patch = {}
    facts = _normalize_fact_items(packet.get("facts"))
    return profile_patch, facts

def _extract_profile_updates(state: AgentState, current_profile: Dict[str, Any]) -> tuple[Dict[str, Any], List[str]]:
    deterministic_patch, deterministic_facts = _deterministic_profile_patch(state.get("user_message", ""))

    if state.get("use_llm", True):
        llm_patch, llm_facts = _llm_extract_memory_packet(state, current_profile)
    else:
        llm_patch, llm_facts = {}, []

    profile_patch = {**llm_patch, **deterministic_patch}
    fact_lines = [*llm_facts, *deterministic_facts]
    if not profile_patch and not fact_lines:
        return current_profile, []
    return _normalize_profile_patch(profile_patch, current_profile), fact_lines

def _profile_fact_text(field: str, value: Any, lang: str = "ru") -> str | None:
    if value in (None, "", []):
        return None
    if field == "user_name":
        return _localized(lang, f"Пользователя зовут {value}.", f"The user's name is {value}.", f"Пайдаланушының аты {value}.")
    if field == "age":
        return _localized(lang, f"Пользователю {value} лет.", f"The user is {value} years old.", f"Пайдаланушы {value} жаста.")
    if field == "favorite_subjects":
        joined = ", ".join(value) if isinstance(value, list) else str(value)
        return _localized(lang, f"Профильные предметы: {joined}.", f"Preferred subjects: {joined}.", f"Бейіндік пәндер: {joined}.")
    if field == "activity_type":
        return _localized(lang, f"Предпочтительный формат работы: {value}.", f"Preferred work style: {value}.", f"Ұнайтын жұмыс форматы: {value}.")
    if field == "career_goal":
        return _localized(lang, f"Интересующая сфера: {value}.", f"Target field: {value}.", f"Қызықтыратын сала: {value}.")
    if field == "ent_score":
        return _localized(lang, f"ЕНТ: {value}.", f"UNT score: {value}.", f"ҰБТ балы: {value}.")
    if field == "language":
        return _localized(lang, f"Предпочтительный язык обучения: {value}.", f"Preferred study language: {value}.", f"Қалаулы оқу тілі: {value}.")
    if field == "budget":
        return _localized(lang, f"Предпочтительный формат оплаты: {value}.", f"Preferred payment option: {value}.", f"Қалаулы төлем форматы: {value}.")
    return None


def _upsert_session_fact(session: Dict[str, Any], text: str, source: str = "memory") -> None:
    cleaned = _normalize_spaces(text).strip()
    if not cleaned:
        return
    normalized = cleaned.lower()
    facts = session.setdefault("facts", [])
    for fact in facts:
        if fact.get("normalized") == normalized:
            fact["source"] = source
            return
    facts.append({"text": cleaned, "normalized": normalized, "source": source})


def _sync_profile_facts(session: Dict[str, Any], before: Dict[str, Any], after: Dict[str, Any], lang: str) -> None:
    for field in ["user_name", "age", "favorite_subjects", "activity_type", "career_goal", "ent_score", "language", "budget"]:
        before_value = before.get(field)
        after_value = after.get(field)
        if after_value in (None, "", []):
            continue
        if after_value != before_value:
            fact_text = _profile_fact_text(field, after_value, lang)
            if fact_text:
                _upsert_session_fact(session, fact_text, field)


def _remember_user_facts(state: AgentState) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
    session = state["session"]
    before = dict(session.get("profile", {}))
    profile, fact_lines = _extract_profile_updates(state, before)

    session["profile"] = profile
    _sync_profile_facts(session, before, profile, state["lang"])
    for fact in fact_lines:
        _upsert_session_fact(session, fact)

    return profile, session.get("facts", [])




def _deterministic_profile_patch(text: str) -> tuple[Dict[str, Any], List[str]]:
    """Extract high-confidence applicant facts without asking the LLM."""
    raw = text or ""
    lower = raw.lower()
    patch: Dict[str, Any] = {}
    facts: List[str] = []

    age_match = re.search(r"(?:мне|маған|i am|i'm)\s+(\d{1,2})\s*(?:лет|жас|years? old)?", lower)
    if age_match:
        age = int(age_match.group(1))
        if 12 <= age <= 99:
            patch["age"] = age
            facts.append(f"Пользователю {age} лет.")

    ent_match = re.search(r"(\d{2,3})\s*(?:балл(?:ов|а)?\s*)?(?:ент|ұбт|unt)\b", lower)
    if not ent_match:
        ent_match = re.search(r"(?:ент|ұбт|unt)\D{0,24}(\d{2,3})", lower)
    if ent_match:
        score = int(ent_match.group(1))
        if 0 <= score <= 140:
            patch["ent_score"] = score
            facts.append(f"ЕНТ: {score}.")

    subjects: List[str] = []
    known_subjects = [
        "математика", "информатика", "физика", "химия", "биология", "география",
        "история", "английский", "казахский", "русский", "литература",
    ]
    subject_zone = lower
    m = re.search(r"(?:профильные\s+предметы|предметы|пәндер|subjects)\s*[:\-–—]?\s*([^.;\n]+)", lower)
    if m:
        subject_zone = m.group(1)
    for subject in known_subjects:
        if subject in subject_zone and subject not in subjects:
            subjects.append(subject)
    if subjects:
        patch["favorite_subjects"] = subjects
        facts.append("Профильные предметы: " + ", ".join(subjects) + ".")

    lang_match = re.search(r"(?:язык\s+обучения|оқу\s+тілі|study\s+language)\s*[:\-–—]?\s*([а-яa-zәіңғүұқөһ]+)", lower)
    if lang_match:
        lang = lang_match.group(1).strip()
        if lang:
            patch["language"] = lang
            facts.append(f"Предпочтительный язык обучения: {lang}.")

    return patch, facts


def _semantic_turn_analysis(state: AgentState) -> Dict[str, Any]:
    """Contextual intent analysis for the latest turn.

    This replaces brittle keyword gates. The classifier receives the latest
    message, reply reference, recent raw history, active flow and current
    profile, then returns a compact semantic decision that the planner can use.
    """
    cached = state.get("turn_semantics")
    if isinstance(cached, dict) and cached:
        return cached

    if looks_like_calculation(state.get("user_message", "")):
        result = {
            "intent": "calculation",
            "route": "calculator",
            "needs_lookup": False,
            "confidence": 1.0,
            "task": state.get("user_message", "").strip(),
            "lookup_query": "",
        }
        state["turn_semantics"] = result
        return result

    if not state.get("use_llm", True):
        result = {"intent": "unknown", "route": "", "needs_lookup": False, "confidence": 0.0, "task": "", "lookup_query": ""}
        state["turn_semantics"] = result
        return result

    session = state.get("session", {})
    profile = state.get("profile") or session.get("profile", {})
    reply_to = state.get("reply_to") or {}
    active_flow = session.get("active_flow")
    messages = [
        {
            "role": "system",
            "content": (
                "You are a semantic turn controller for a university applicant assistant. "
                "Infer the user's current intent from meaning, conversation history, selected reply reference, "
                "active flow, and applicant profile. Do not do keyword matching. Do not let the active flow override "
                "a new factual question. A selected reply is context only; the latest user message still controls the task. "
                "Return JSON only: {"
                "\"intent\":\"memory_profile|calculation|factual_university_question|personalized_recommendation|"
                "recommendation_followup_more|recommendation_followup_missing_info|profile_data|social|other\","
                "\"route\":\"general|calculator|knowledge|recommendation|interview|career|scoring|lookup\","
                "\"needs_lookup\":true|false,"
                "\"lookup_query\":\"short search query if official data is needed\","
                "\"task\":\"one sentence task for the selected agent\","
                "\"confidence\":0.0}. "
                "factual_university_question means the user asks for official rules, thresholds, grants, payments, documents, dates, contacts, costs, programs as a catalog fact, or other institutional facts. "
                "personalized_recommendation means the user asks what program suits the applicant profile. "
                "recommendation_followup_more means the user is continuing a previous recommendation answer and wants additional options beyond already listed ones. "
                "recommendation_followup_missing_info means the user asks what information is still needed to refine the recommendation. "
                "memory_profile means the user asks what is remembered about them. "
                "Choose knowledge/lookup for factual university information. Choose recommendation only for personalized program matching or direct recommendation follow-up."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Latest user message:\n{state.get('user_message', '')}\n\n"
                f"Selected reply reference:\n{reply_to or '-'}\n\n"
                f"Recent raw messages:\n{_recent_history(session, limit=12) or '-'}\n\n"
                f"Relevant memory/context:\n{_memory_context(state, include_history=False) or '-'}\n\n"
                f"Current applicant profile:\n{profile}\n\n"
                f"Active flow:\n{active_flow}\n\n"
                f"Profile complete:\n{_profile_is_complete(profile)}"
            ),
        },
    ]
    raw = generate_from_messages(messages, max_new_tokens=180, ctx_texts=None)
    payload = _extract_json_object(raw)
    if not isinstance(payload, dict):
        payload = {}

    route = str(payload.get("route") or "").strip().lower()
    if route not in ROUTE_NAMES:
        route = ""
    intent = str(payload.get("intent") or "other").strip().lower()
    allowed_intents = {
        "memory_profile",
        "calculation",
        "factual_university_question",
        "personalized_recommendation",
        "recommendation_followup_more",
        "recommendation_followup_missing_info",
        "profile_data",
        "social",
        "other",
    }
    if intent not in allowed_intents:
        intent = "other"
    try:
        confidence = float(payload.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    result = {
        "intent": intent,
        "route": route,
        "needs_lookup": bool(payload.get("needs_lookup", False)),
        "lookup_query": str(payload.get("lookup_query") or "").strip(),
        "task": _normalize_spaces(str(payload.get("task") or "")).strip(),
        "confidence": confidence,
        "raw": raw,
    }
    state["turn_semantics"] = result
    return result


def _semantic_intent(state: AgentState) -> str:
    return str(_semantic_turn_analysis(state).get("intent") or "other")


def _semantic_route(state: AgentState) -> str | None:
    analysis = _semantic_turn_analysis(state)
    route = str(analysis.get("route") or "").strip().lower()
    confidence = float(analysis.get("confidence") or 0.0)
    return route if route in ROUTE_NAMES and confidence >= 0.45 else None


def _is_memory_profile_query(state: AgentState) -> bool:
    return _semantic_intent(state) == "memory_profile"


def _is_admission_fact_query(state: AgentState) -> bool:
    return _semantic_intent(state) == "factual_university_question"


def _is_missing_profile_followup(state: AgentState) -> bool:
    return _semantic_intent(state) == "recommendation_followup_missing_info"


def _is_more_recommendations_followup(state: AgentState) -> bool:
    return _semantic_intent(state) == "recommendation_followup_more"


def _wants_program_recommendation(state: AgentState | str) -> bool:
    if isinstance(state, dict):
        return _semantic_intent(state) == "personalized_recommendation"
    # Non-state calls are only used as a last-resort fallback when the LLM is disabled.
    return False


def _has_partial_recommendation_profile(profile: Dict[str, Any], user_message: str = "") -> bool:
    """Return True when the stored applicant profile already contains usable recommendation context.

    This intentionally does not inspect fixed phrases in user_message. The current turn's
    intent is handled by the semantic router; this helper only checks durable profile
    state so the recommendation flow does not become a keyword tree.
    """
    if not isinstance(profile, dict):
        return False
    durable_fields = (
        "favorite_subjects",
        "ent_score",
        "career_goal",
        "activity_type",
        "interests",
        "language",
        "budget",
    )
    return any(profile.get(field) not in (None, "", [], {}) for field in durable_fields)


def _forced_route(state: AgentState) -> str | None:
    route = _semantic_route(state)
    if route in {"general", "calculator", "knowledge", "recommendation"}:
        return route
    return None

def _fallback_route(state: AgentState) -> str:
    session = state["session"]
    active_flow = session.get("active_flow")
    profile = session.get("profile", {})
    user_text = state["user_message"]

    forced = _forced_route(state)
    if forced:
        return forced
    if state.get("needs_lookup") or active_flow == "lookup":
        return "lookup"
    if active_flow == INTERVIEW_FLOW_MARKER:
        if _wants_program_recommendation(state) or (_has_partial_recommendation_profile(profile, user_text) and _semantic_route(state) == "recommendation"):
            return "recommendation"
        return "interview"
    if looks_like_calculation(user_text):
        return "calculator"
    if active_flow == "recommendation":
        if _is_admission_fact_query(state):
            return "knowledge"
        if _is_missing_profile_followup(state) or _is_more_recommendations_followup(state):
            return "recommendation"
        if _wants_program_recommendation(state) or (_has_partial_recommendation_profile(profile, user_text) and _semantic_route(state) == "recommendation"):
            return "recommendation"
        return "knowledge"
    if active_flow == "career":
        return "career"
    if active_flow == "scoring":
        return "scoring"
    if _wants_program_recommendation(state) or (_has_partial_recommendation_profile(profile, user_text) and _semantic_route(state) == "recommendation"):
        return "recommendation"
    return "knowledge"


def _llm_route_decision(state: AgentState) -> str:
    forced = _forced_route(state)
    if forced:
        return forced

    session = state["session"]
    profile = session.get("profile", {})
    messages = [
        {
            "role": "system",
            "content": (
                "You are a routing controller for a university applicant assistant built with LangGraph. "
                f"The assistant represents {UNIVERSITY_NAME_EN} in Karaganda, Kazakhstan. "
                "Choose exactly one route for the next step based on the latest user message, conversation history, "
                "current applicant profile, and active flow. "
                "Use general for greetings, remembering user name, assistant identity, and casual conversational turns. "
                "Use interview when the user is actually answering profiling questions, shares exam score or subjects, explicitly asks for personalized matching, "
                "or is in an active interview flow and asks what options exist, says they do not know, or asks you to suggest choices. "
                "Use knowledge for factual university or admission questions, even if the applicant profile is still incomplete. "
                "Use career for professions and career outcomes. "
                "Use scoring for admission chances and exam scores. "
                "Use calculator for arithmetic calculations or when the user asks to compute an expression. "
                "Use lookup when another mode is likely missing specific university data and should first search the knowledge base. "
                "Use recommendation for recommending programs from a completed profile. "
                "For this university and Kazakhstan, prefer ENТ over EGE in reasoning. "
                "If the user asks a factual follow-up about price, cost, documents, address, contacts, deadlines, or grant details, use knowledge rather than recommendation. "
                "Do not choose interview just because the profile is incomplete if the latest turn is a factual question about the university. "
                "But if active flow is interview and the user asks for guidance choosing among possible directions, keep interview. "
                "Return exactly one lowercase word: general, interview, knowledge, career, scoring, recommendation, calculator, lookup."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Latest user message:\n{state['user_message']}\n\n"
                f"Conversation history:\n{_recent_history(session)}\n\n"
                f"Known memory facts:\n{_recent_facts(session) or '-'}\n\n"
                f"Current profile:\n{profile}\n\n"
                f"Active flow:\n{session.get('active_flow')}\n\n"
                f"Profile complete:\n{_profile_is_complete(profile)}"
            ),
        },
    ]
    raw = generate_from_messages(messages, max_new_tokens=12, ctx_texts=None).strip().lower()
    route = _extract_one_of(raw, ["general", "interview", "knowledge", "career", "scoring", "recommendation", "calculator", "lookup"])
    if route:
        return route
    return _fallback_route(state)


def extract_memory(state: AgentState) -> AgentState:
    profile, facts = _remember_user_facts(state)
    return {
        "session": state["session"],
        "profile": profile,
        "facts": facts,
        "profile_complete": _profile_is_complete(profile),
    }


def _extract_planner_payload(raw: str) -> Dict[str, Any]:
    payload = _extract_json_object(raw)
    return payload if isinstance(payload, dict) else {}


def plan_turn(state: AgentState) -> AgentState:
    session = state["session"]
    profile = state.get("profile", session.get("profile", {}))
    facts_text = _recent_facts(session) or "-"
    history_text = _recent_history(session) or "-"

    forced_route = _forced_route(state)
    if forced_route == "general":
        task = state["user_message"].strip()
        return {
            "session": session,
            "profile": profile,
            "facts": session.get("facts", []),
            "profile_complete": _profile_is_complete(profile),
            "next_node": "general",
            "route": "general",
            "needs_lookup": False,
            "lookup_query": "",
            "task": task,
            "planner_notes": "deterministic memory/profile route",
        }

    turn_kind = _llm_turn_kind(state)

    if not state.get("use_llm", True):
        route = _fallback_route(state)
        task = state["user_message"].strip()
        return {
            "session": session,
            "profile": profile,
            "facts": session.get("facts", []),
            "profile_complete": _profile_is_complete(profile),
            "next_node": route,
            "route": route,
            "task": task,
            "planner_notes": "",
        }

    messages = [
        {
            "role": "system",
            "content": (
                "You are a planner for a university assistant graph. "
                f"The assistant represents {UNIVERSITY_NAME_EN} in Karaganda, Kazakhstan. "
                "First understand the user's message using memory and history. "
                "Then decide whether lookup is needed, whether the mode should change, and what concrete task the next node should perform. "
                "Return JSON only with this schema: "
                "{\"route\":\"general|interview|knowledge|career|scoring|recommendation|calculator|lookup\","
                "\"needs_lookup\":true|false,"
                "\"lookup_query\":\"string\","
                "\"task\":\"string\","
                "\"planner_notes\":\"string\"}. "
                "The task must be actionable and specific for the selected route. "
                "If the user is unsure during interview, task should say to offer grounded choices from the knowledge base rather than ask abstractly. "
                "If the question requires official university facts, prefer knowledge or lookup. "
                "If active flow is interview and the user asks what options exist or says they do not know, keep interview and set task to propose concrete options. "
                "If the latest turn is a factual question about university rules, benefits, eligibility, documents, housing, costs, contacts, dates, or official conditions, do not route to interview even if an interview was active before."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Latest user message:\n{state['user_message']}\n\n"
                f"Conversation history:\n{history_text}\n\n"
                f"Known profile:\n{profile}\n\n"
                f"Known memory facts:\n{facts_text}\n\n"
                f"Turn memory context:\n{_memory_context(state, include_history=True) or '-'}\n\n"
                f"Active flow:\n{session.get('active_flow')}\n\n"
                f"Profile complete:\n{_profile_is_complete(profile)}"
            ),
        },
    ]
    raw = generate_from_messages(messages, max_new_tokens=220, ctx_texts=None)
    payload = _extract_planner_payload(raw)

    route = str(payload.get("route") or "").strip().lower()
    if route not in ROUTE_NAMES:
        route = _llm_route_decision(state)
        if route not in ROUTE_NAMES:
            route = _fallback_route(state)

    needs_lookup = bool(payload.get("needs_lookup", False))
    lookup_query = str(payload.get("lookup_query") or "").strip()
    task = _normalize_spaces(str(payload.get("task") or "")).strip() or state["user_message"].strip()
    planner_notes = _normalize_spaces(str(payload.get("planner_notes") or "")).strip()

    semantics = _semantic_turn_analysis(state)
    semantic_intent = str(semantics.get("intent") or "other")
    semantic_route = _semantic_route(state)
    if semantic_route in ROUTE_NAMES and semantic_intent in {
        "memory_profile",
        "calculation",
        "factual_university_question",
        "personalized_recommendation",
        "recommendation_followup_more",
        "recommendation_followup_missing_info",
    }:
        route = semantic_route
        needs_lookup = bool(semantics.get("needs_lookup", needs_lookup))
        lookup_query = str(semantics.get("lookup_query") or lookup_query).strip()
        task = str(semantics.get("task") or task).strip()
        planner_notes = (planner_notes + " | semantic turn analysis").strip(" |")

    if route == "lookup":
        needs_lookup = True
    if route in {"knowledge", "calculator"} and not lookup_query:
        lookup_query = _lookup_basis(state)
    if needs_lookup and not lookup_query:
        lookup_query = _lookup_basis(state)
    if _is_admission_fact_query(state):
        route = "knowledge"
        needs_lookup = True
        lookup_query = lookup_query or _lookup_basis(state)
        task = "Find official admission threshold scores and answer the latest factual question, not the earlier recommendation task."
    if turn_kind == "factual" and route in {"interview", "general", "recommendation"}:
        route = "knowledge"
        needs_lookup = True
        lookup_query = lookup_query or _lookup_basis(state)
        task = task or "Find grounded university facts and answer from the knowledge base."
    if (
        not _is_admission_fact_query(state)
        and _wants_program_recommendation(state)
        and _has_partial_recommendation_profile(profile, state["user_message"])
    ):
        route = "recommendation"
        needs_lookup = False
        task = task or "Recommend programs from the partial applicant profile and mention missing details."
    if session.get("active_flow") == INTERVIEW_FLOW_MARKER and turn_kind == "profile_reply" and route in {"general", "knowledge"}:
        route = "interview"
        needs_lookup = False
    if turn_kind == "social" and route == "interview":
        route = "general"

    return {
        "session": session,
        "profile": profile,
        "facts": session.get("facts", []),
        "profile_complete": _profile_is_complete(profile),
        "next_node": route,
        "route": route,
        "needs_lookup": needs_lookup,
        "lookup_query": lookup_query,
        "task": task,
        "planner_notes": planner_notes,
    }


def _is_contact_query(text: str) -> bool:
    return service_is_contact_query(text)

def _direct_contact_context(query: str) -> List[str]:
    return service_direct_contact_context(
        query,
        query_stems=_query_stems,
        text_structure_bonus=_text_structure_bonus,
        payload_context_snippet=_payload_context_snippet,
    )

def _safe_university_fallback(lang: str) -> str:
    return service_safe_university_fallback(lang, UNIVERSITY_NAME_RU, UNIVERSITY_NAME_EN)

def _generate_lookup_queries(query: str) -> List[str]:
    return service_generate_lookup_queries(query, strip_leading_list_marker=_strip_leading_list_marker)

def _build_lookup_query_queue(query: str) -> List[str]:
    return service_build_lookup_query_queue(
        query,
        normalize_spaces=_normalize_spaces,
        strip_leading_list_marker=_strip_leading_list_marker,
    )

def _normalize_lookup_text(text: str) -> str:
    return service_normalize_lookup_text(text)

def _lookup_basis(state: AgentState) -> str:
    basis = service_lookup_basis(state, recent_history=_recent_history)
    return _memory_augmented_lookup_query(state, basis)

def _query_stems(query: str) -> set[str]:
    return service_query_stems(query, simple_words=_simple_words)

def _text_structure_bonus(query: str, text: str) -> int:
    return service_text_structure_bonus(
        query,
        text,
        contains_numbered_list=_contains_numbered_list,
        looks_like_phone=_looks_like_phone,
        has_url_or_email=_has_url_or_email,
        contains_large_number=_contains_large_number,
        contains_year=_contains_year,
    )

def _path_bonus(query: str, path_to_file: str) -> int:
    return service_path_bonus(query, path_to_file, query_stems=_query_stems, simple_words=_simple_words)

def _payload_matches_filters(payload: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    return service_payload_matches_filters(payload, filters)

def _retrieve_knowledge_context(query: str, history_text: str = "") -> List[str]:
    return service_retrieve_knowledge_context(
        query,
        history_text,
        llm_retrieval_filters=_llm_retrieval_filters,
        direct_contact_context=_direct_contact_context,
        query_stems=_query_stems,
        path_bonus=_path_bonus,
        text_structure_bonus=_text_structure_bonus,
        payload_context_snippet=_payload_context_snippet,
        payload_matches_filters=_payload_matches_filters,
        generate_lookup_queries=_generate_lookup_queries,
    )

def _targeted_document_context(query: str, limit: int = 4) -> List[str]:
    return service_targeted_document_context(
        query,
        text_structure_bonus=_text_structure_bonus,
        path_bonus=_path_bonus,
        payload_context_snippet=_payload_context_snippet,
        contains_numbered_list=_contains_numbered_list,
    )[:limit]

def _extract_address_answer(query: str, ctx_list: List[str], lang: str) -> str | None:
    return service_extract_address_answer(
        query,
        ctx_list,
        lang,
        normalize_spaces=_normalize_spaces,
        normalize_lookup_text=_normalize_lookup_text,
    )

def _extract_document_answer(query: str, ctx_list: List[str], lang: str) -> str | None:
    return service_extract_document_answer(
        query,
        ctx_list,
        lang,
        targeted_document_context=_targeted_document_context,
        normalize_lookup_text=_normalize_lookup_text,
        normalize_spaces=_normalize_spaces,
        strip_leading_list_marker=_strip_leading_list_marker,
    )

def _render_profile_report(profile: Dict[str, Any], lang: str) -> str:
    parts = []
    user_name = profile.get("user_name")
    if user_name:
        parts.append(_localized(lang, f"имя: {user_name}", f"name: {user_name}", f"аты: {user_name}"))
    if profile.get("age") is not None:
        parts.append(_localized(lang, f"возраст: {profile['age']}", f"age: {profile['age']}", f"жасы: {profile['age']}"))
    if profile.get("favorite_subjects"):
        joined = ", ".join(profile["favorite_subjects"])
        parts.append(_localized(lang, f"предметы: {joined}", f"subjects: {joined}", f"пәндер: {joined}"))
    if profile.get("activity_type"):
        parts.append(
            _localized(
                lang,
                f"тип деятельности: {profile['activity_type']}",
                f"preferred activity type: {profile['activity_type']}",
                f"ұнайтын жұмыс форматы: {profile['activity_type']}",
            )
        )
    if profile.get("career_goal"):
        parts.append(
            _localized(
                lang,
                f"интересующая сфера: {profile['career_goal']}",
                f"target field: {profile['career_goal']}",
                f"қызықтыратын сала: {profile['career_goal']}",
            )
        )
    if profile.get("ent_score") is not None:
        parts.append(_localized(lang, f"ЕНТ: {profile['ent_score']}", f"UNT score: {profile['ent_score']}", f"ҰБТ: {profile['ent_score']}"))
    if profile.get("language"):
        parts.append(
            _localized(
                lang,
                f"язык обучения: {profile['language']}",
                f"study language: {profile['language']}",
                f"оқу тілі: {profile['language']}",
            )
        )
    if profile.get("budget"):
        budget_value = profile["budget"]
        parts.append(
            _localized(
                lang,
                f"формат оплаты: {budget_value}",
                f"budget mode: {budget_value}",
                f"оқу форматы: {budget_value}",
            )
        )
    if not parts:
        return _localized(
            lang,
            "Пока профиль почти не заполнен.",
            "The profile is still almost empty.",
            "Профиль әлі толық толтырылмаған.",
        )
    intro = _localized(
        lang,
        "Сейчас у меня такой профиль:",
        "Here is the current profile I have:",
        "Қазір менде мынадай профиль бар:",
    )
    return intro + " " + "; ".join(parts) + "."


def _looks_wrong_language(text: str, lang: str) -> bool:
    if lang == "en":
        return False
    latin_count = _latin_word_count(text)
    cyrillic_count = _cyrillic_word_count(text)
    if latin_count == 0:
        return False
    return latin_count > cyrillic_count and len(text) > 40

def _generate_interview_followup(state: AgentState, profile: Dict[str, Any], field: str, changed: bool) -> str:
    return service_generate_interview_followup(
        state,
        profile,
        field,
        changed,
        field_anchor=_field_anchor,
        field_question_seed=_field_question_seed,
        retrieve_knowledge_context=_retrieve_knowledge_context,
        recent_history=_recent_history,
        recent_facts=_recent_facts,
        localized=_localized,
        lang_label=_lang_label,
        generate_interview_choices=_generate_interview_choices,
        looks_wrong_language=_looks_wrong_language,
        normalize_spaces=_normalize_spaces,
    )

def _alternate_interview_prompt(field: str, lang: str, attempt: int) -> str:
    return service_alternate_interview_prompt(
        field,
        lang,
        attempt,
        field_question_seed=_field_question_seed,
        field_anchor=_field_anchor,
        field_prompt=_field_prompt,
    )

def _general_reply(state: AgentState, profile: Dict[str, Any]) -> str:
    return service_general_reply(
        state,
        profile,
        detect_meta_intent_fn=_detect_meta_intent,
        localized=_localized,
        recent_history=_recent_history,
        recent_facts=_recent_facts,
        render_profile_report=_render_profile_report,
        assistant_name_reply_fn=_assistant_name_reply,
        assistant_identity_reply_fn=_assistant_identity_reply,
        assistant_location_reply_fn=_assistant_location_reply,
        is_greeting_fn=_is_greeting,
        university_name_ru=UNIVERSITY_NAME_RU,
        university_name_en=UNIVERSITY_NAME_EN,
        university_location_ru=UNIVERSITY_LOCATION_RU,
    )

def general_agent(state: AgentState) -> AgentState:
    session = state["session"]
    profile = state.get("profile", session["profile"])
    session["profile"] = profile
    answer = _general_reply(state, profile)
    append_raw_message(session, role="assistant", content=answer)
    return {
        "session": session,
        "profile": profile,
        "facts": session.get("facts", []),
        "answer": answer,
        "profile_complete": _profile_is_complete(profile),
        "route": "general",
    }


def interview_agent(state: AgentState) -> AgentState:
    session = state["session"]
    before = dict(session["profile"])
    profile = state.get("profile", session["profile"])
    session["profile"] = profile
    session["active_flow"] = INTERVIEW_FLOW_MARKER

    next_field = _next_missing_field(profile)
    if next_field:
        changed = profile != before
        field, fallback = next_field
        repeat_count = int(session.get("interview_repeat_count", 0))
        if session.get("last_interview_field") == field and not changed:
            repeat_count += 1
        else:
            repeat_count = 1

        answer = _generate_interview_followup(state, profile, field, changed) or fallback
        if not changed and session.get("last_interview_field") == field:
            answer = _alternate_interview_prompt(field, state["lang"], repeat_count)
        preliminary = _brief_preliminary_directions(profile, state["user_message"], state["lang"])
        if preliminary and preliminary not in answer:
            answer = f"{preliminary}\n\n{answer}"

        session["last_interview_field"] = field
        session["last_interview_question"] = answer
        session["interview_repeat_count"] = repeat_count
        append_raw_message(session, role="assistant", content=answer)
        return {
            "session": session,
            "profile": profile,
            "facts": session.get("facts", []),
            "answer": answer,
            "profile_complete": False,
            "route": "interview",
        }

    session["active_flow"] = "recommendation"
    session["last_interview_field"] = None
    session["last_interview_question"] = None
    session["interview_repeat_count"] = 0
    return {
        "session": session,
        "profile": profile,
        "facts": session.get("facts", []),
        "profile_complete": True,
        "next_node": "scoring",
        "route": "interview",
    }


def knowledge_agent(state: AgentState) -> AgentState:
    session = state["session"]
    turn_kind = _llm_turn_kind(state)
    meta_intent = _detect_meta_intent(state)
    if meta_intent == "university_identity":
        answer = _assistant_identity_reply(state["lang"])
        append_raw_message(session, role="assistant", content=answer)
        session["active_flow"] = "knowledge"
        return {
            "session": session,
            "retrieved_context": [],
            "answer": answer,
            "profile_complete": _profile_is_complete(session["profile"]),
            "route": "knowledge",
            "needs_lookup": False,
        }
    if meta_intent == "location_context":
        answer = _assistant_location_reply(state["lang"])
        append_raw_message(session, role="assistant", content=answer)
        session["active_flow"] = "knowledge"
        return {
            "session": session,
            "retrieved_context": [],
            "answer": answer,
            "profile_complete": _profile_is_complete(session["profile"]),
            "route": "knowledge",
            "needs_lookup": False,
        }

    if turn_kind == "factual":
        needs_lookup = True
    elif state["use_llm"]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a university assistant controller. "
                    f"The assistant represents {UNIVERSITY_NAME_EN} in Karaganda, Kazakhstan. "
                    "Decide whether the latest user message requires searching the university knowledge base. "
                    "Return only yes or no. "
                    "Return yes for factual university questions where the answer should come from official university materials. "
                    "Return no for general knowledge, casual conversation, or questions answerable without the university knowledge base. "
                    "Use the planner task as the main objective for this turn."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Latest user message:\n{state['user_message']}\n\n"
                    f"Recent conversation:\n{_recent_history(session)}\n\n"
                    f"Planner task:\n{state.get('task', '-')}\n\n"
                    f"Turn memory context:\n{_memory_context(state, include_history=True) or '-'}"
                ),
            },
        ]
        raw = generate_from_messages(messages, max_new_tokens=8, ctx_texts=None).strip().lower()
        needs_lookup = "yes" in raw
    else:
        needs_lookup = True

    if needs_lookup:
        lookup_basis = _lookup_basis(state)
        return {
            "session": session,
            "answer": f"Сначала проверю материалы {UNIVERSITY_NAME_RU}.",
            "profile_complete": _profile_is_complete(session["profile"]),
            "route": "knowledge",
            "needs_lookup": True,
            "lookup_query": lookup_basis,
            "lookup_queries": _build_lookup_query_queue(lookup_basis),
            "lookup_iteration": 0,
            "next_node": "lookup",
        }

    if state["use_llm"]:
        messages = [
            {
                "role": "system",
                "content": (
                    f"You are an assistant for {UNIVERSITY_NAME_EN} in Karaganda, Kazakhstan. "
                    "If the question is general knowledge outside university admissions, give a short helpful answer in 1-2 sentences, "
                    "then gently note that your main focus is university programs, admission, grants, and applicant guidance. "
                    "Do not invent university-specific facts. "
                    "If the user asks about exams for this university, refer to ENТ, not EGE. "
                    "Follow the planner task closely."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Language: {state['lang']}.\n"
                    f"User question: {state['user_message']}\n"
                    f"Planner task: {state.get('task', '-')}\n\n"
                    f"Recent conversation:\n{_recent_history(session)}\n\n"
                    f"Applicant/session memory:\n{_memory_context(state) or '-'}\n\n"
                    f"University identity: {UNIVERSITY_NAME_RU}, {UNIVERSITY_LOCATION_RU}."
                ),
            },
        ]
        answer = generate_from_messages(messages, max_new_tokens=100, ctx_texts=None)
    else:
        answer = _safe_university_fallback(state["lang"])

    append_raw_message(session, role="assistant", content=answer)
    session["active_flow"] = "knowledge"
    return {
        "session": session,
        "retrieved_context": [],
        "answer": answer,
        "profile_complete": _profile_is_complete(session["profile"]),
        "route": "knowledge",
        "needs_lookup": False,
    }


def _profile_text(profile: Dict[str, Any]) -> str:
    return service_profile_text(profile)


def _missing_profile_fields(profile: Dict[str, Any], lang: str = "ru") -> List[str]:
    return service_missing_profile_fields(profile, lang)


def _build_recommendations(profile: Dict[str, Any], user_message: str = "") -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    return service_build_recommendations(profile, user_message)


def _recommendation_missing_note(missing: List[str], lang: str) -> str:
    if not missing:
        return ""
    return _localized(
        lang,
        f"\n\nЭто предварительная рекомендация. Для более точного подбора еще нужно уточнить: {', '.join(missing)}.",
        f"\n\nThis is a preliminary recommendation. To make it more accurate, I still need: {', '.join(missing)}.",
        f"\n\nБұл алдын ала ұсыныс. Дәлірек таңдау үшін мынаны нақтылау керек: {', '.join(missing)}.",
    )


def _deterministic_recommendation_answer(recommendations: List[Dict[str, Any]], missing: List[str], lang: str) -> str:
    lines = [
        _localized(
            lang,
            f"{index}. {item['program']}. Причины: {', '.join(item['reasons'])}.",
            f"{index}. {item['program']}. Reasons: {', '.join(item['reasons'])}.",
            f"{index}. {item['program']}. Себептері: {', '.join(item['reasons'])}.",
        )
        for index, item in enumerate(recommendations, start=1)
    ]
    intro = _localized(
        lang,
        "По уже известным данным можно ориентироваться на такие программы:\n",
        "Based on the details already known, you can look at these programs:\n",
        "Белгілі деректер бойынша мына бағдарламаларды қарастыруға болады:\n",
    )
    return intro + "\n".join(lines) + _recommendation_missing_note(missing, lang)




def _missing_profile_answer(missing: List[str], lang: str) -> str:
    if not missing:
        return _localized(
            lang,
            "Для базовой рекомендации основные данные уже есть. Дальше можно уточнять только предпочтения: язык обучения, грант/платное, формат работы и конкретную IT-сферу.",
            "The core data for a basic recommendation is already present. Further refinement only needs preferences: study language, grant/paid mode, preferred activity format, and specific IT field.",
            "Негізгі ұсыныс үшін басты деректер бар. Енді тек оқу тілі, грант/ақылы оқу, жұмыс форматы және нақты IT саласын нақтылауға болады.",
        )
    return _localized(
        lang,
        "Для более точного подбора еще не хватает: " + ", ".join(missing) + ".",
        "For a more accurate match, the missing details are: " + ", ".join(missing) + ".",
        "Дәлірек таңдау үшін әлі керек: " + ", ".join(missing) + ".",
    )


def _additional_recommendation_answer(scoring_table: List[Dict[str, Any]], lang: str, offset: int = 3, limit: int = 5) -> str:
    extra_items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in scoring_table[offset:]:
        program = str((item.get("program") or {}).get("name") or "").strip()
        if not program or program in seen:
            continue
        seen.add(program)
        extra_items.append({"program": program, "reasons": list(item.get("reasons") or [])[:3] or ["есть частичное совпадение профиля"]})
        if len(extra_items) >= limit:
            break
    if not extra_items:
        return _localized(
            lang,
            "В текущей базе после уже названных вариантов нет достаточно надежных дополнительных рекомендаций по этому профилю.",
            "In the current database, there are no sufficiently reliable additional recommendations for this profile beyond the already listed options.",
            "Қазіргі база бойынша бұрын аталған нұсқалардан бөлек бұл профильге жеткілікті сенімді қосымша ұсыныстар жоқ.",
        )
    intro = _localized(
        lang,
        "Кроме уже названных вариантов можно дополнительно посмотреть:\n",
        "Besides the already listed options, you can also look at:\n",
        "Бұрын аталған нұсқалардан бөлек мыналарды қарастыруға болады:\n",
    )
    lines = []
    for index, item in enumerate(extra_items, start=1):
        reasons = ", ".join(str(reason) for reason in item["reasons"] if reason)
        lines.append(
            _localized(
                lang,
                f"{index}. {item['program']}. Причины: {reasons}.",
                f"{index}. {item['program']}. Reasons: {reasons}.",
                f"{index}. {item['program']}. Себептері: {reasons}.",
            )
        )
    return intro + "\n".join(lines)

def _has_visible_recommendation_score(answer: str) -> bool:
    text = answer or ""
    numeric_score_patterns = [
        r"\b\d{1,3}(?:[\.,]\d+)?\s*%",
        r"\b\d{1,3}(?:[\.,]\d+)?\s*(?:балл|балла|баллов|points?|score)",
        r"\b(?:совместимость|сәйкестік|compatibility)\s*[-:—]?\s*\d",
    ]
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in numeric_score_patterns)


def _brief_preliminary_directions(profile: Dict[str, Any], user_message: str, lang: str) -> str:
    if not _has_partial_recommendation_profile(profile, user_message):
        return ""
    try:
        _, recommendations = _build_recommendations(profile, user_message)
    except Exception:
        return ""
    if not recommendations:
        return ""
    names = ", ".join(str(item.get("program") or "").strip() for item in recommendations[:3] if item.get("program"))
    if not names:
        return ""
    return _localized(
        lang,
        f"Предварительно уже можно смотреть в сторону: {names}.",
        f"Preliminarily, you can already look toward: {names}.",
        f"Алдын ала мына бағыттарды қарастыруға болады: {names}.",
    )


def scoring_agent(state: AgentState) -> AgentState:
    profile = state["session"]["profile"]
    scoring_table, recommendations = _build_recommendations(profile, state["user_message"])
    return {
        "scoring_table": scoring_table,
        "recommendations": recommendations,
        "next_node": "recommendation",
        "profile_complete": _profile_is_complete(profile),
        "route": "scoring",
    }


def recommendation_agent(state: AgentState) -> AgentState:
    recommendations = state.get("recommendations") or []
    scoring_table = state.get("scoring_table") or []
    profile = state["session"]["profile"]
    lang = state["lang"]
    missing = _missing_profile_fields(profile, lang)
    if (not recommendations or not scoring_table) and _has_partial_recommendation_profile(profile, state["user_message"]):
        scoring_table, recommendations = _build_recommendations(profile, state["user_message"])

    if _is_missing_profile_followup(state):
        answer = _missing_profile_answer(missing, lang)
    elif _is_more_recommendations_followup(state):
        answer = _additional_recommendation_answer(scoring_table, lang)
    elif not recommendations:
        answer = _localized(
            lang,
            "Пока данных мало, но я все равно могу дать предварительное направление после любого ответа. "
            f"Сейчас не хватает: {', '.join(missing)}.",
            "There is very little data, but I can still give a preliminary direction after any answer. "
            f"I still need: {', '.join(missing)}.",
            "Дерек аз болса да, әр жауаптан кейін алдын ала бағыт бере аламын. "
            f"Әлі керек: {', '.join(missing)}.",
        )
    elif not state["use_llm"]:
        answer = _deterministic_recommendation_answer(recommendations, missing, lang)
    else:
        deterministic_answer = _deterministic_recommendation_answer(recommendations, missing, lang)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a recommendation agent for university applicants. "
                    "Explain top-3 recommendations factually and naturally without inventing new programs. "
                    "If the applicant profile is incomplete, still give preliminary recommendations from the available data, "
                    "then briefly name the missing fields needed for a more accurate match. "
                    "Answer strictly in the requested language. "
                    "Do not show numeric match scores, compatibility percentages, points, or internal ranking values. "
                    "Do not switch to English unless the requested language is English."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Language: {_lang_label(lang)}.\n"
                    f"Applicant profile: {profile}.\n"
                    f"Session memory:\n{_memory_context(state) or '-'}\n"
                    f"Top recommendations: {recommendations}.\n"
                    f"Missing profile fields: {missing}.\n"
                    "Write a concise natural answer with 3 numbered items. "
                    "For each item include only the program name and 1-2 reasons. "
                    "Do not include percentages, points, баллы, compatibility values, or any numeric match score. "
                    "Do not refuse because of missing fields; present the result as preliminary when fields are missing. "
                    "If the user asks in Russian or Kazakh, keep the whole answer in that language."
                ),
            },
        ]
        answer = generate_from_messages(messages, ctx_texts=None)
        if _looks_wrong_language(answer, lang) or _has_visible_recommendation_score(answer):
            answer = deterministic_answer

    session = state["session"]
    append_raw_message(session, role="assistant", content=answer)
    session["active_flow"] = "recommendation"
    return {
        "session": session,
        "answer": answer,
        "recommendations": recommendations,
        "profile_complete": _profile_is_complete(profile),
        "route": "recommendation",
    }


def lookup_agent(state: AgentState) -> AgentState:
    session = state["session"]
    lookup_queries = state.get("lookup_queries") or _build_lookup_query_queue(state.get("lookup_query") or _lookup_basis(state))
    lookup_iteration = max(0, state.get("lookup_iteration", 0))
    if lookup_iteration >= len(lookup_queries):
        lookup_iteration = max(0, len(lookup_queries) - 1)

    query = lookup_queries[lookup_iteration] if lookup_queries else (state.get("lookup_query") or _lookup_basis(state))
    ctx_list = _retrieve_knowledge_context(query, _recent_history(session))

    deterministic_answer = _extract_address_answer(state["user_message"], ctx_list, state["lang"])
    if deterministic_answer is None:
        deterministic_answer = _extract_document_answer(state["user_message"], ctx_list, state["lang"])

    if deterministic_answer:
        answer = deterministic_answer
    elif looks_like_calculation(state["user_message"]) and ctx_list:
        try:
            expression, result = calculate(f"{state['user_message']}\n\n" + "\n".join(ctx_list))
            if any(char.isalpha() for char in result):
                answer = f"Посчитал по данным из базы знаний: {result}. Формула: {expression}."
            else:
                answer = f"Посчитал по данным из базы знаний: {expression} = {result}."
        except CalculatorError:
            answer = (
                "Я выполнил поиск по базе знаний, но для точного расчёта данных всё ещё недостаточно. "
                "Уточните, пожалуйста, недостающие значения."
            )
    elif ctx_list:
        if state["use_llm"]:
            try:
                answer = generate_answer(
                    state["user_message"],
                    ctx_list,
                    state["lang"],
                    history_text=_recent_history(session),
                    memory_text=_memory_context(state, include_history=False),
                )
            except Exception:
                answer = "\n\n---\n\n".join(ctx_list[:3])
        else:
            answer = "\n\n---\n\n".join(ctx_list)
    else:
        answer = ""

    should_retry = not ctx_list and lookup_iteration + 1 < len(lookup_queries)
    if should_retry:
        return {
            "session": session,
            "answer": answer or "Уточняю поисковый запрос по базе знаний.",
            "retrieved_context": [],
            "profile_complete": _profile_is_complete(session["profile"]),
            "route": "lookup",
            "needs_lookup": True,
            "lookup_query": state.get("lookup_query") or state["user_message"],
            "lookup_queries": lookup_queries,
            "lookup_iteration": lookup_iteration + 1,
            "next_node": "lookup",
        }

    if not ctx_list and state.get("allow_web_search"):
        web_ctx = search_whitelisted_web(state["user_message"])
        if web_ctx:
            ctx_list = web_ctx
            answer = (
                "\n\n---\n\n".join(ctx_list[:3])
                if state["use_llm"]
                else "\n\n---\n\n".join(ctx_list)
            )
            if state["use_llm"]:
                try:
                    answer = generate_answer(
                        state["user_message"],
                        ctx_list,
                        state["lang"],
                        history_text=_recent_history(session),
                        memory_text=_memory_context(state, include_history=False),
                    )
                except Exception:
                    pass

    if not answer:
        answer = (
            "Я выполнил несколько поисков по базе знаний, но не нашёл достаточно точных данных для ответа. "
            "Уточните, пожалуйста, формулировку запроса."
        )

    append_raw_message(session, role="assistant", content=answer)
    session["active_flow"] = "lookup"
    return {
        "session": session,
        "answer": answer,
        "retrieved_context": ctx_list,
        "profile_complete": _profile_is_complete(session["profile"]),
        "route": "lookup",
        "needs_lookup": False,
        "lookup_query": state.get("lookup_query") or state["user_message"],
        "lookup_queries": lookup_queries,
        "lookup_iteration": lookup_iteration,
    }


def deterministic_lookup_answer(message: str, lang: str = "ru", history_text: str = "") -> str | None:
    """Best-effort non-LLM answer used when cloud generation is unavailable."""
    try:
        ctx_list = _retrieve_knowledge_context(message, history_text)
    except Exception:
        ctx_list = []
    try:
        answer = _extract_address_answer(message, ctx_list, lang)
        if answer:
            return answer
        answer = _extract_document_answer(message, ctx_list, lang)
        if answer:
            return answer
    except Exception:
        pass
    if ctx_list:
        return "\n\n---\n\n".join(ctx_list[:3])
    return None


def calculator_agent(state: AgentState) -> AgentState:
    session = state["session"]
    try:
        expression, result = calculate(state["user_message"])
        if any(char.isalpha() for char in result):
            answer = f"Посчитал: {result}. Формула: {expression}."
        else:
            answer = f"Результат: {expression} = {result}."
        needs_lookup = False
    except CalculatorError as exc:
        answer = (
            "Для точного расчёта мне не хватает данных. "
            "Сначала попробую найти недостающую информацию в базе знаний."
        )
        needs_lookup = True

    if not needs_lookup:
        append_raw_message(session, role="assistant", content=answer)
        session["active_flow"] = "calculator"
    return {
        "session": session,
        "answer": answer,
        "profile_complete": _profile_is_complete(session["profile"]),
        "route": "calculator",
        "needs_lookup": needs_lookup,
        "lookup_query": state["user_message"],
        "lookup_queries": _build_lookup_query_queue(state["user_message"]) if needs_lookup else [],
        "lookup_iteration": 0 if needs_lookup else state.get("lookup_iteration", 0),
        "next_node": "lookup" if needs_lookup else "calculator",
    }


def career_agent(state: AgentState) -> AgentState:
    ctx = find_passage(state["user_message"], get_index())
    ctx_list = [ctx] if isinstance(ctx, str) else (ctx or [])
    recommendations = state.get("recommendations") or []
    messages = [
        {
            "role": "system",
            "content": "You are a career guidance agent. Answer only using the supplied context and recommendation data.",
        },
        {
            "role": "user",
            "content": (
                f"Language: {state['lang']}.\n"
                f"Question: {state['user_message']}.\n"
                f"Relevant context: {ctx_list}.\n"
                f"Current recommendations: {recommendations}.\n"
                "Give a practical answer about possible careers. If there is not enough data, say so explicitly."
            ),
        },
    ]
    answer = (
        generate_answer(
            state["user_message"],
            ctx_list,
            state["lang"],
            history_text=_recent_history(session),
            memory_text=_memory_context(state, include_history=False),
        )
        if state["use_llm"] and ctx_list
        else (ctx_list[0] if ctx_list else ANSWER_NOT_FOUND)
    )
    session = state["session"]
    append_raw_message(session, role="assistant", content=answer)
    session["active_flow"] = "career"
    return {
        "session": session,
        "answer": answer,
        "retrieved_context": ctx_list,
        "profile_complete": _profile_is_complete(session["profile"]),
        "route": "career",
    }
