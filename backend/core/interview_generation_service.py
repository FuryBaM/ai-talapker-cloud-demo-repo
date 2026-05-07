from __future__ import annotations

from typing import Any, Callable

from core.agent_state import AgentState
from core.generation import generate_from_messages


def generate_interview_followup(
    state: AgentState,
    profile: dict[str, Any],
    field: str,
    changed: bool,
    *,
    field_anchor: Callable[[str], dict[str, str]],
    field_question_seed: Callable[[str, str], str],
    retrieve_knowledge_context: Callable[[str, str], list[str]],
    recent_history: Callable[[dict[str, Any], int], str],
    recent_facts: Callable[[dict[str, Any], int], str],
    localized: Callable[[str, str, str, str | None], str],
    lang_label: Callable[[str], str],
    generate_interview_choices: Callable[[AgentState, dict[str, Any], str, list[str]], list[str]],
    looks_wrong_language: Callable[[str, str], bool],
    normalize_spaces: Callable[[Any], str],
) -> str:
    lang = state["lang"]
    field_meta = field_anchor(field)
    fallback = field_question_seed(field, lang)
    anchor_context = retrieve_knowledge_context(field_meta.get("query", ""), recent_history(state["session"], 8))[:2]
    choice_options = generate_interview_choices(state, profile, field, anchor_context)
    anchor_block = "\n\n---\n\n".join(anchor_context) if anchor_context else ""
    options_block = "\n".join(f"- {item}" for item in choice_options) if choice_options else "-"
    planner_task = state.get("task", "").strip()
    if not state.get("use_llm", True):
        prefix = localized(
            lang,
            "Хорошо, тогда уточню ещё один момент:",
            "Got it, let me ask one more thing:",
            "Жақсы, онда тағы бір нәрсені нақтылайын:",
        )
        return f"{prefix} {fallback}"
    messages = [
        {
            "role": "system",
            "content": (
                "You generate exactly one short follow-up question for an applicant interview. "
                "Ask naturally, vary phrasing, and avoid repetitive templates. "
                "Use the field anchor and any relevant university context if provided. "
                "The goal is to collect user data that can later be matched against the knowledge base and program catalog. "
                "Do not copy the field goal or fallback prompt verbatim. "
                "If the user seems unsure, says they do not know, or asks what options exist, offer 3 to 5 concrete grounded options first and then a short choice question. "
                "When possible, propose examples from the university context or candidate programs instead of abstract categories. "
                "Do not greet again. Do not mention the full university name unless the user asked for it. "
                "Return only one question in the requested language."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Language: {lang_label(lang)}.\n"
                f"Latest user message: {state['user_message']}\n"
                f"Known profile: {profile}\n"
                f"Known memory facts:\n{recent_facts(state['session'], 12) or '-'}\n"
                f"Recent conversation:\n{recent_history(state['session'], 8)}\n\n"
                f"Planner task:\n{planner_task or '-'}\n\n"
                f"Missing field to ask about: {field}\n"
                f"Field anchor: {field_meta.get('anchor', field)}\n"
                f"Field goal: {field_meta.get(lang, field_meta.get('ru', fallback))}\n"
                f"Knowledge snippets for this field:\n{anchor_block or '-'}\n\n"
                f"Concrete grounded options you may offer:\n{options_block}\n\n"
                f"Fallback prompt: {fallback}\n"
                f"The previous user reply {'did update the profile' if changed else 'did not update the missing field clearly'}."
            ),
        },
    ]
    generated = generate_from_messages(messages, max_new_tokens=60, ctx_texts=None).strip()
    if not generated:
        return fallback
    generated = normalize_spaces(generated).strip()
    if looks_wrong_language(generated, lang):
        return fallback
    if "?" not in generated and not generated.endswith("?"):
        generated = generated.rstrip(".! ") + "?"
    return generated


def alternate_interview_prompt(
    field: str,
    lang: str,
    attempt: int,
    *,
    field_question_seed: Callable[[str, str], str],
    field_anchor: Callable[[str], dict[str, str]],
    field_prompt: Callable[[str, str], str],
) -> str:
    base = field_question_seed(field, lang)
    if attempt <= 1:
        return base
    field_meta = field_anchor(field)
    if lang == "en":
        return f"To keep matching your profile with university data, please choose or clarify one suitable option for: {field_meta.get('anchor', field)}."
    if lang == "kk":
        return f"Профильді университет базасымен дәлірек сәйкестендіру үшін мына бағыттардың бірін таңдаңыз не нақтылаңыз: {field_prompt(field, lang)}"
    return f"Чтобы точнее сопоставить ваш профиль с данными университета, выберите или уточните подходящий вариант: {field_prompt(field, lang)}"
