from __future__ import annotations

from typing import Any, Callable

from core.agent_state import AgentState
from core.generation import generate_from_messages


def assistant_name_reply(lang: str, university_name_ru: str, university_name_en: str) -> str:
    if lang == "kk":
        return "Мен Қарағанды қаласындағы Әбілқас Сағынов атындағы Қарағанды техникалық университетінің AI-ассистентімін. Түсу, грант және бағдарламалар бойынша көмектесе аламын."
    if lang == "en":
        return f"I am an AI assistant for {university_name_en}. I can help with admission, programs, and applicant questions."
    return f"Я AI-ассистент {university_name_ru}. Могу помочь с поступлением, программами, грантами и вопросами об университете."


def assistant_identity_reply(
    lang: str,
    university_name_ru: str,
    university_name_en: str,
    university_city_ru: str,
    university_country_ru: str,
) -> str:
    if lang == "en":
        return f"I represent {university_name_en}. It is located in {university_city_ru}, Kazakhstan."
    return f"Я представляю {university_name_ru}. Университет находится в городе {university_city_ru}, {university_country_ru}."


def assistant_location_reply(
    lang: str,
    university_name_ru: str,
    university_city_ru: str,
    university_country_ru: str,
) -> str:
    if lang == "en":
        return f"The university is located in {university_city_ru}, Kazakhstan."
    return f"Речь идет о {university_name_ru} в городе {university_city_ru}, {university_country_ru}."


def is_greeting(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in ["привет", "здравствуйте", "салем", "hello", "hi"])


def looks_like_interview_reply(text: str) -> bool:
    stripped = text.strip()
    if not stripped or "?" in stripped:
        return False
    return len(stripped.split()) <= 10


def llm_turn_kind(
    state: AgentState,
    *,
    recent_history: Callable[[dict[str, Any], int], str],
    recent_facts: Callable[[dict[str, Any], int], str],
    extract_one_of: Callable[..., str | None],
) -> str:
    if not state.get("use_llm", True):
        return "other"
    session = state["session"]
    profile = session.get("profile", {})
    messages = [
        {
            "role": "system",
            "content": (
                "Classify the latest user turn for a university assistant. "
                "Return exactly one lowercase label: factual, profile_reply, social, other. "
                "factual = the user asks for university facts, rules, documents, benefits, costs, contacts, dates, housing, grants, or policies. "
                "profile_reply = the user provides personal data or answers profiling questions such as subjects, score, preferences, interests, language, budget, age, or career direction. "
                "social = greeting, thanks, casual small talk, assistant identity, or memory check. "
                "Use latest message, history, current profile, and active flow."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Latest user message:\n{state['user_message']}\n\n"
                f"Conversation history:\n{recent_history(session, 8)}\n\n"
                f"Known profile:\n{profile}\n\n"
                f"Known memory facts:\n{recent_facts(session, 12) or '-'}\n\n"
                f"Active flow:\n{session.get('active_flow')}"
            ),
        },
    ]
    raw = generate_from_messages(messages, max_new_tokens=8, ctx_texts=None).strip().lower()
    return extract_one_of(raw, ["factual", "profile_reply", "social", "other"], "other") or "other"


def detect_meta_intent(
    state: AgentState,
    *,
    llm_turn_kind_fn: Callable[[AgentState], str],
    recent_history: Callable[[dict[str, Any], int], str],
    recent_facts: Callable[[dict[str, Any], int], str],
    extract_one_of: Callable[..., str | None],
) -> str:
    turn_kind = llm_turn_kind_fn(state)
    if turn_kind in {"profile_reply", "factual"} or not state.get("use_llm", True):
        return "none"
    session = state["session"]
    profile = session.get("profile", {})
    messages = [
        {
            "role": "system",
            "content": (
                "Classify the latest user turn for a university assistant. "
                "Return exactly one lowercase label: "
                "user_name_query, assistant_name, university_identity, location_context, profile_report, exam_context, greeting, none. "
                "Use conversation history, known profile, and memory facts. "
                "Do not explain."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Latest user message:\n{state['user_message']}\n\n"
                f"Conversation history:\n{recent_history(session, 8)}\n\n"
                f"Known profile:\n{profile}\n\n"
                f"Known memory facts:\n{recent_facts(session, 12) or '-'}"
            ),
        },
    ]
    raw = generate_from_messages(messages, max_new_tokens=12, ctx_texts=None).strip().lower()
    return extract_one_of(
        raw,
        ["user_name_query", "assistant_name", "university_identity", "location_context", "profile_report", "exam_context", "greeting", "none"],
        "none",
    ) or "none"


def general_reply(
    state: AgentState,
    profile: dict[str, Any],
    *,
    detect_meta_intent_fn: Callable[[AgentState], str],
    localized: Callable[[str, str, str, str | None], str],
    recent_history: Callable[[dict[str, Any], int], str],
    recent_facts: Callable[[dict[str, Any], int], str],
    render_profile_report: Callable[[dict[str, Any], str], str],
    assistant_name_reply_fn: Callable[[str], str],
    assistant_identity_reply_fn: Callable[[str], str],
    assistant_location_reply_fn: Callable[[str], str],
    is_greeting_fn: Callable[[str], bool],
    university_name_ru: str,
    university_name_en: str,
    university_location_ru: str,
) -> str:
    text = state["user_message"].lower().strip()
    lang = state["lang"]
    user_name = profile.get("user_name")
    meta_intent = detect_meta_intent_fn(state)

    if meta_intent == "profile_report" or "что ты запомнил" in text or "что запомнил" in text or "что ты помнишь" in text or "обо мне" in text:
        return render_profile_report(profile, lang)
    if meta_intent == "user_name_query":
        return localized(lang, f"Да, вас зовут {user_name}.", f"Yes, your name is {user_name}.", f"Иә, сіздің атыңыз {user_name}.") if user_name else localized(lang, "Вы пока не называли имя.", "You have not told me your name yet.", "Сіз әлі атыңызды айтпадыңыз.")
    if "запомнил" in text:
        return render_profile_report(profile, lang)
    if meta_intent == "assistant_name":
        return assistant_name_reply_fn(lang)
    if meta_intent == "university_identity":
        return assistant_identity_reply_fn(lang)
    if meta_intent == "location_context":
        return assistant_location_reply_fn(lang)
    if meta_intent == "exam_context":
        return localized(
            lang,
            f"Для поступления сюда ориентиром является ЕНТ, а не ЕГЭ. Речь идет о {university_name_ru} в {university_location_ru}.",
            f"For admission here, the relevant exam is UNT, not EGE. I am talking about {university_name_en} in {university_location_ru}.",
            f"Мұнда түсу үшін ЕГЭ емес, ҰБТ маңызды. Әңгіме {university_name_ru}, {university_location_ru} туралы болып тұр.",
        )
    if user_name and meta_intent == "greeting":
        return localized(
            lang,
            f"Приятно познакомиться, {user_name}. Я помогу с поступлением в {university_name_ru}, программами, грантами и документами.",
            f"Nice to meet you, {user_name}. I can help with admission to {university_name_en}, programs, grants, and documents.",
            f"Танысқаныма қуаныштымын, {user_name}. Мен {university_name_ru} бойынша түсу, бағдарламалар, гранттар және құжаттар туралы көмектесемін.",
        )
    if meta_intent == "greeting" or is_greeting_fn(text):
        return localized(
            lang,
            f"Здравствуйте. Я AI-ассистент {university_name_ru}. Помогу с поступлением, программами, грантами и документами.",
            f"Hello. I am the AI assistant for {university_name_en}. I can help with admission, programs, grants, and documents.",
            f"Сәлеметсіз бе. Мен {university_name_ru} бойынша AI-көмекшімін. Түсу, бағдарламалар, гранттар және құжаттар туралы көмектесемін.",
        )
    if "ты повторяешься" in text:
        return localized(lang, "Да, заметил. Переформулирую ответы точнее.", "Yes, I noticed. I will phrase the next answers more naturally.", "Иә, байқадым. Келесі жауаптарды табиғиырақ құрамын.")
    if text in {"реально?", "серьезно?", "серьёзно?"}:
        return localized(lang, "Да.", "Yes.", "Иә.")

    messages = [
        {
            "role": "system",
            "content": (
                f"You are a concise conversational assistant for {university_name_en} in Karaganda, Kazakhstan. "
                "Use the known profile and recent history as memory. "
                "Do not invent user facts. "
                "Do not act like a generic assistant from Russia. "
                "If the user asks about identity, location, admission system, or exam type, stay consistent with Kazakhstan, the university, and ENТ. "
                "Avoid repetitive filler phrases."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Language: {state['lang']}.\n"
                f"Latest user message: {state['user_message']}\n"
                f"Conversation history:\n{recent_history(state['session'], 8)}\n\n"
                f"Known memory facts:\n{recent_facts(state['session'], 12) or '-'}\n\n"
                f"Known user name: {user_name}\n"
                f"University identity: {university_name_ru}, {university_location_ru}.\n"
                "Answer naturally in 1-2 short sentences."
            ),
        },
    ]
    answer = generate_from_messages(messages, max_new_tokens=80, ctx_texts=None)
    return answer or "Я помогу с выбором программы, поступлением и вопросами об университете."
