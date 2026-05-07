import json
import re
from typing import List

from core.config import ANSWER_GUARD_ENABLED, MAX_NEW_TOKENS, SYSTEM_PROMPT
from core.model_store import (
    gen_model,
    gen_tokenizer,
    guard_im_end_token_id,
    guard_model,
    guard_tokenizer,
    im_end_token_id,
)
from core.rag import QdrantIndex


def _question_type_hint(question: str) -> str:
    lower = question.lower()
    if any(token in lower for token in ["документ", "документы", "перечень", "справка", "копия"]):
        return "documents"
    if any(token in lower for token in ["адрес", "контакт", "телефон", "email", "e-mail", "где находится"]):
        return "contacts"
    if any(token in lower for token in ["цена", "стоимость", "оплата", "сколько стоит"]):
        return "tuition"
    if any(token in lower for token in ["грант", "льгот", "скидк", "стипенд"]):
        return "benefits"
    if any(token in lower for token in ["срок", "дата", "когда", "дедлайн"]):
        return "timeline"
    if any(token in lower for token in ["общежит", "засел", "проживан"]):
        return "housing"
    return "general_university_fact"



def _expected_behavior_hint(question: str, ctx_texts: List[str] | None) -> str:
    qtype = _question_type_hint(question)
    if qtype == "documents":
        return (
            "Answer with a clean list of required documents only. "
            "Do not include service labels, source metadata, or unrelated sections such as rules, priorities, or explanations unless the question asks for them."
        )
    if qtype == "contacts":
        return "Return the exact contact details or address from context. Do not infer a city, address, or phone number."
    if qtype == "tuition":
        return "Return the exact price, year, and study format if present. Do not estimate or generalize."
    if qtype == "benefits":
        return "Describe eligibility and conditions as university policy, not as a property of a program."
    if qtype == "timeline":
        return "Return exact dates or periods from context. Do not paraphrase them into vague timing."
    if qtype == "housing":
        return "Focus on dormitory conditions, procedures, or documents only, and keep unrelated sections out."
    if ctx_texts:
        return "Use only grounded facts from the retrieved context and synthesize them into a natural answer."
    return "If the context is insufficient, say so directly without inventing facts."


def _prompt_guidance_block(question: str, ctx_texts: List[str] | None) -> str:
    context_found = "yes" if ctx_texts else "no"
    return (
        f"Question type: {_question_type_hint(question)}\n"
        f"RAG context found: {context_found}\n"
        "RAG context will be provided separately below.\n"
        f"Expected behavior: {_expected_behavior_hint(question, ctx_texts)}"
    )


def build_chat_messages(question: str, ctx_texts: List[str] | None, lang: str, memory_text: str | None = None) -> List[dict]:
    ctx = "\n\n---\n\n".join(str(item or "") for item in (ctx_texts or []))
    guidance = _prompt_guidance_block(question, ctx_texts)
    memory_text = (memory_text or "").strip()
    memory_block = memory_text if memory_text else "-"
    user_prompt = (
        f"Answer in language: {lang}.\n"
        "Use official RAG context below as the only source for university facts. "
        "Use applicant/session memory only to personalize the answer and resolve references like \"my score\", \"my subjects\", or \"what suits me\". "
        "Do not treat memory as official university policy.\n"
        "Write a complete, natural answer, not just keywords.\n"
        "Format the answer in Markdown. Use numbered or bulleted lists when listing multiple items. "
        "Put every main item and sub-item on its own line; do not write a long list as one paragraph.\n"
        "If the context contains several items, mention them all in a readable list or in a full sentence.\n"
        "For questions about documents, requirements, contacts, addresses, dates, prices, or scores, give the exact facts from the context.\n"
        "Do not answer with generic phrases like 'usually required' or 'typically'.\n"
        "Do not use placeholders such as [city], [address], or similar.\n"
        "If the context supports only part of the answer, provide the supported part and briefly say what detail is not visible in the available materials.\n"
        "Keep the answer concise but informative, usually 2-6 sentences unless the context is very short.\n"
        "Use neutral wording like 'the university' or just name the programs directly.\n"
        "If the context is a policy, rule, benefit regulation, or eligibility condition, describe it as a university policy or condition, not as a property of an educational program.\n"
        "Do not repeat or paraphrase the user's question as if that were the answer.\n"
        "Do not write 'your university', 'our university', or add unsupported conclusions about the programs.\n"
        "If the context does not contain the answer, reply exactly: "
        "There is not enough information in the database to answer this question.\n\n"
        f"Guidance:\n{guidance}\n\n"
        f"Applicant/session memory:\n{memory_block}\n\n"
        f"Context:\n{ctx}\n\n"
        f"Question: {question}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _stop_token_ids(tokenizer, im_end_token: int | None) -> List[int]:
    token_ids = [tokenizer.eos_token_id]
    if im_end_token is not None:
        token_ids.append(im_end_token)
    return token_ids


def _decode_new_tokens(output_ids, input_length: int, tokenizer) -> str:
    generated_tokens = output_ids[input_length:]
    return tokenizer.decode(generated_tokens, skip_special_tokens=True).strip()


def _normalize_institution_phrasing(text: str) -> str:
    replacements = [
        (r"\b[Вв]аш(?:его|ему|ем|е|и|их)? университет\b", "Университет"),
        (r"\b[Нн]аш(?:его|ему|ем|е|и|их)? университет\b", "Университет"),
        (r"\b[Вв]аш университет\b", "Университет"),
        (r"\b[Нн]аш университет\b", "Университет"),
    ]
    normalized = text
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized)
    return normalized


def _clean_answer(text: str, ctx_texts: List[str] | None = None) -> str:
    cleaned = re.sub(r"<[^>]+>", "", text).strip()
    cleaned = re.sub(r"\b(?:Human|User|Assistant)\s*:\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\[[^\]]+\]", "", cleaned).strip()
    cleaned = _normalize_institution_phrasing(cleaned)

    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    deduped = []
    for part in parts:
        if part and (not deduped or part.strip() != deduped[-1].strip()):
            deduped.append(part.strip())

    stitched = " ".join(deduped).strip() if deduped else cleaned
    if stitched and not re.search(r"[.!?]\s*$", stitched):
        last_punct = max(stitched.rfind("."), stitched.rfind("!"), stitched.rfind("?"))
        if last_punct != -1:
            stitched = stitched[: last_punct + 1].strip()
    return stitched


def _looks_like_question_echo(question: str, answer: str) -> bool:
    q_words = {
        token
        for token in re.findall(r"\w+", question.lower(), flags=re.UNICODE)
        if len(token) >= 4
    }
    a_words = {
        token
        for token in re.findall(r"\w+", answer.lower(), flags=re.UNICODE)
        if len(token) >= 4
    }
    if not q_words or not a_words:
        return False
    overlap = len(q_words & a_words) / max(1, len(q_words))
    return overlap >= 0.8 and len(a_words - q_words) <= 2


def _fallback_answer(lang: str) -> str:
    if lang == "kk":
        return "Қолжетімді университет материалдарында бұл сұраққа нақты жауап жоқ. Сұрақты нақтылап жазыңыз."
    if lang == "en":
        return "There is not enough information in the available university materials to answer this question."
    return "В доступных материалах университета нет точного ответа на этот вопрос. Уточните, пожалуйста, запрос."


def _generate_raw_from_messages(
    messages: List[dict],
    max_new_tokens: int = MAX_NEW_TOKENS,
    *,
    model=None,
    tokenizer=None,
    im_end_token: int | None = None,
) -> str:
    model = model or gen_model
    tokenizer = tokenizer if tokenizer is not None else gen_tokenizer

    if hasattr(model, "generate_chat"):
        return model.generate_chat(messages, max_new_tokens=max_new_tokens).strip()

    if tokenizer is None:
        raise RuntimeError("Transformers generation requires a tokenizer; GGUF model must expose generate_chat().")

    import torch

    if im_end_token is None and tokenizer is gen_tokenizer:
        im_end_token = im_end_token_id
    inputs = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        tokenize=True,
        return_tensors="pt",
        return_dict=True,
    ).to(model.device)

    with torch.inference_mode():
        output = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            repetition_penalty=1.1,
            use_cache=True,
            eos_token_id=_stop_token_ids(tokenizer, im_end_token),
            pad_token_id=tokenizer.pad_token_id,
        )

    return _decode_new_tokens(output[0], inputs["input_ids"].shape[-1], tokenizer)


def _extract_json(text: str) -> dict:
    if not text:
        return {}
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def llm_answer_guard(
    question: str,
    draft_answer: str,
    ctx_texts: List[str] | None,
    lang: str,
    history_text: str | None = None,
    memory_text: str | None = None,
) -> str:
    cleaned_draft = _clean_answer(draft_answer, ctx_texts)
    if not cleaned_draft:
        return _fallback_answer(lang)
    if not ANSWER_GUARD_ENABLED or guard_model is None:
        if _looks_like_question_echo(question, cleaned_draft):
            return _fallback_answer(lang)
        return cleaned_draft
    if not ctx_texts:
        return cleaned_draft

    context = "\n\n---\n\n".join(str(item or "") for item in ctx_texts)
    guidance = _prompt_guidance_block(question, ctx_texts)
    history_block = history_text or ""
    memory_block = (memory_text or "").strip() or "-"
    messages = [
        {
            "role": "system",
            "content": (
                "You are a final answer safety and grounding filter for a university assistant. "
                "Evaluate a draft answer against the user question, recent history, and retrieved university context. "
                "You must do three things at once: decide, forbid unsupported claims, and rewrite when needed. "
                "If the draft is fully supported, keep it concise and natural. "
                "If only part of the answer is supported, keep the supported part and gently note that the remaining detail is not visible in the available materials. "
                "If part of the draft is unsupported, remove the unsupported part and rewrite the answer using only supported facts. "
                "Use block only when the draft cannot be salvaged at all from the context. "
                "If the draft mostly repeats or paraphrases the user question without adding grounded facts from context, do not allow it; rewrite or block it. "
                "Never allow placeholders like [city], [address], <...>, or generic claims like 'usually required' when the user asked for university facts. "
                "Treat the guidance block as a compact hint describing what kind of question it is and what answer behavior is expected. "
                "Return JSON only with this schema: "
                "{\"decision\":\"allow|rewrite|block\",\"answer\":\"final answer text\"}."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Language: {lang}\n\n"
                f"Question:\n{question}\n\n"
                f"Recent history:\n{history_block}\n\n"
                f"Applicant/session memory:\n{memory_block}\n\n"
                f"Guidance:\n{guidance}\n\n"
                f"Retrieved context:\n{context}\n\n"
                f"Draft answer:\n{cleaned_draft}"
            ),
        },
    ]
    raw = _generate_raw_from_messages(
        messages,
        max_new_tokens=220,
        model=guard_model,
        tokenizer=guard_tokenizer,
        im_end_token=guard_im_end_token_id,
    )
    payload = _extract_json(raw)
    decision = str(payload.get("decision") or "").strip().lower()
    candidate = _clean_answer(str(payload.get("answer") or ""), ctx_texts)

    if decision in {"allow", "rewrite"} and candidate:
        if _looks_like_question_echo(question, candidate):
            return _fallback_answer(lang)
        return candidate
    if decision == "block":
        return _fallback_answer(lang)
    if candidate:
        if _looks_like_question_echo(question, candidate):
            return _fallback_answer(lang)
        return candidate
    if cleaned_draft:
        if _looks_like_question_echo(question, cleaned_draft):
            return _fallback_answer(lang)
        return cleaned_draft
    return _fallback_answer(lang)


def generate_from_messages(
    messages: List[dict],
    max_new_tokens: int = MAX_NEW_TOKENS,
    ctx_texts: List[str] | None = None,
) -> str:
    answer = _generate_raw_from_messages(messages, max_new_tokens=max_new_tokens)
    return _clean_answer(answer, ctx_texts)


def generate_answer(
    question: str,
    ctx_texts: List[str] | None,
    lang: str,
    max_new_tokens: int = MAX_NEW_TOKENS,
    history_text: str | None = None,
    memory_text: str | None = None,
) -> str:
    messages = build_chat_messages(question, ctx_texts, lang, memory_text=memory_text)
    draft = generate_from_messages(messages, max_new_tokens=max_new_tokens, ctx_texts=ctx_texts)
    return llm_answer_guard(question, draft, ctx_texts, lang, history_text=history_text, memory_text=memory_text)


def _suggestion_lang_label(lang: str) -> str:
    if lang == "en":
        return "English"
    if lang == "kk":
        return "Kazakh"
    return "Russian"


def _parse_suggestion_lines(generated: str, count: int) -> List[str]:
    items: List[str] = []
    seen = set()

    def push(raw: str) -> None:
        text = re.sub(r"<[^>]+>", "", str(raw or "")).strip()
        text = re.sub(r"^[-*•]+\s*", "", text)
        text = re.sub(r"^\d+[.)]\s*", "", text)
        text = re.sub(r"^['\"]|['\"]$", "", text).strip()
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return
        if text[-1] != "?":
            text = text.rstrip(".!:; ") + "?"
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        items.append(text)

    source = str(generated or "")
    source = re.sub(r"\s+(?=\d+[.)]\s+)", "\n", source)
    source = re.sub(r"\s+(?=[-*•]\s+)", "\n", source)
    for line in source.splitlines():
        line = line.strip()
        if not line:
            continue
        question_parts = re.findall(r"[^?]+\?", line)
        if question_parts:
            for part in question_parts:
                push(part)
                if len(items) >= count:
                    return items[:count]
        else:
            push(line)
        if len(items) >= count:
            break
    return items[:count]


def fallback_suggestions(lang: str, count: int) -> List[str]:
    safe_count = max(1, min(count, 8))
    lang_label = _suggestion_lang_label(lang)
    messages = [
        {"role": "system", "content": "You generate user question suggestions for a university assistant."},
        {
            "role": "user",
            "content": (
                f"Return exactly {safe_count} short, natural questions in {lang_label}.\n"
                "These should be useful first prompts for an applicant.\n"
                "Focus on programs, admission, grants, scores, student life, contacts, and career paths.\n"
                "Each line must start with '- '.\n"
                "Do not include numbering, explanations, placeholders, or comments."
            ),
        },
    ]
    generated = generate_from_messages(messages, max_new_tokens=180, ctx_texts=None)
    items = _parse_suggestion_lines(generated, safe_count)
    if items:
        return items

    if lang == "en":
        seed = "What programs are available?"
    elif lang == "kk":
        seed = "Қандай білім беру бағдарламалары бар?"
    else:
        seed = "Какие образовательные программы доступны?"
    return [seed for _ in range(safe_count)]


def suggestion_context(index: QdrantIndex, max_chunks: int = 8) -> List[str]:
    if not index:
        return []

    picked = []
    seen_paths = set()
    points, _ = index.client.scroll(
        collection_name=index.collection_name,
        limit=max(50, max_chunks * 10),
        with_payload=True,
        with_vectors=False,
    )
    for point in points:
        payload = point.payload or {}
        text = payload.get("text")
        path_to_file = payload.get("source_file") or payload.get("path_to_file") or payload.get("doc_id")
        if not text or path_to_file in seen_paths:
            continue
        seen_paths.add(path_to_file)
        picked.append(text)
        if len(picked) >= max_chunks:
            break
    return picked


def _history_block(history: List[dict] | None, limit: int = 10) -> str:
    if not history:
        return ""
    lines = []
    for item in history[-limit:]:
        role = str(item.get("role") or "user")
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def generate_session_suggestions(
    index: QdrantIndex,
    lang: str,
    history: List[dict] | None,
    count: int = 5,
) -> List[str]:
    safe_count = max(1, min(count, 8))
    history_text = _history_block(history)
    if not history_text:
        return generate_suggestions(index, lang, safe_count)

    ctx_list = suggestion_context(index, max_chunks=6)
    lang_label = _suggestion_lang_label(lang)
    messages = [
        {"role": "system", "content": "You generate useful next-question suggestions for a university assistant chat."},
        {
            "role": "user",
            "content": (
                f"Return exactly {safe_count} short, natural follow-up questions in {lang_label}.\n"
                "Base them on the current conversation so they feel relevant to the user's present task.\n"
                "If helpful, use the university context below, but do not invent facts.\n"
                "Prefer next-step questions about admission, documents, grants, programs, deadlines, dormitory, or contacts.\n"
                "Avoid repeating questions that already appeared in the conversation.\n"
                "Each line must start with '- '.\n"
                "Do not include numbering, explanations, placeholders, or comments.\n\n"
                f"Conversation:\n{history_text}\n\n"
                f"University context:\n{chr(10).join(ctx_list)}"
            ),
        },
    ]
    generated = generate_from_messages(messages, max_new_tokens=220, ctx_texts=ctx_list)
    items = _parse_suggestion_lines(generated, safe_count)

    seen_in_history = history_text.lower()
    filtered = [item for item in items if item.lower() not in seen_in_history]
    if len(filtered) < safe_count:
        for item in generate_suggestions(index, lang, safe_count):
            if item.lower() not in seen_in_history and item not in filtered:
                filtered.append(item)
            if len(filtered) >= safe_count:
                break
    return filtered[:safe_count]


def generate_suggestions(index: QdrantIndex, lang: str, count: int = 5) -> List[str]:
    safe_count = max(1, min(count, 8))
    ctx_list = suggestion_context(index)
    if not ctx_list:
        return fallback_suggestions(lang, safe_count)

    lang_label = _suggestion_lang_label(lang)
    messages = [
        {"role": "system", "content": "You generate user question suggestions for a university assistant."},
        {
            "role": "user",
            "content": (
                "Use only the facts from the context.\n"
                f"Return exactly {safe_count} short, natural questions in {lang_label}.\n"
                "Each line must start with '- '.\n"
                "Do not include numbering, explanations, placeholders, or facts not present in context.\n\n"
                f"Context:\n{chr(10).join(ctx_list)}"
            ),
        },
    ]

    generated = generate_from_messages(messages, max_new_tokens=220, ctx_texts=ctx_list)
    items = _parse_suggestion_lines(generated, safe_count)

    if len(items) < safe_count:
        for question in fallback_suggestions(lang, safe_count):
            if question not in items:
                items.append(question)
            if len(items) >= safe_count:
                break
    return items[:safe_count]
