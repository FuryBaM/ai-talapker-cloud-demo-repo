from __future__ import annotations

from typing import Any

import numpy as np

from core.knowledge_assets import get_index, get_programs
from core.model_store import embed_model
from core.rag import search_debug
from core.text_utils import simple_words


def profile_text(profile: dict[str, Any]) -> str:
    parts = []
    for key in ["favorite_subjects", "activity_type", "career_goal", "ent_score", "language", "budget", "interests"]:
        value = profile.get(key)
        if isinstance(value, list):
            if value:
                parts.append(f"{key}: {', '.join(str(item) for item in value if item)}")
        elif value not in (None, "", []):
            parts.append(f"{key}: {value}")
    return "; ".join(parts)


def missing_profile_fields(profile: dict[str, Any], lang: str = "ru") -> list[str]:
    missing: list[str] = []
    labels = {
        "favorite_subjects": {"ru": "профильные предметы", "en": "favorite subjects", "kk": "бейіндік пәндер"},
        "activity_type": {"ru": "предпочитаемый формат деятельности", "en": "preferred activity type", "kk": "ұнайтын жұмыс форматы"},
        "career_goal": {"ru": "интересующая сфера", "en": "career goal", "kk": "қызықтыратын сала"},
        "ent_score": {"ru": "балл ЕНТ", "en": "UNT score", "kk": "ҰБТ балы"},
        "language": {"ru": "язык обучения", "en": "language of study", "kk": "оқу тілі"},
        "budget": {"ru": "грант или платное", "en": "grant or paid study", "kk": "грант па, ақылы оқу ма"},
    }
    for field, localized in labels.items():
        if not profile.get(field):
            missing.append(localized.get(lang, localized["ru"]))
    return missing


def build_recommendations(profile: dict[str, Any], user_message: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profile_text_value = profile_text(profile) or user_message
    profile_embedding = embed_model.encode(
        f"query: {profile_text_value}",
        convert_to_numpy=True,
        normalize_embeddings=True,
    )

    scoring_table = []
    profile_subjects = " ".join(profile.get("favorite_subjects") or []).lower()
    profile_activity = " ".join([profile.get("activity_type") or "", *(profile.get("interests") or [])]).lower()
    career_goal = (profile.get("career_goal") or "").lower()
    ent_score = profile.get("ent_score")

    candidate_hits = search_debug(
        profile_text_value,
        get_index(),
        top_k=24,
        domains=["programs"],
        schemas=["program_entry", "program_text"],
    )
    candidate_programs = []
    seen_programs = set()
    for hit in candidate_hits:
        program_name = str(hit.get("title") or "").strip()
        if not program_name or program_name in seen_programs:
            continue
        seen_programs.add(program_name)
        text_value = str(hit.get("text") or "")
        metadata = dict(hit.get("metadata", {}) or {})
        tags = [token for token in simple_words(text_value.lower()) if len(token) > 3][:12]
        candidate_programs.append(
            {
                "name": program_name,
                "tags": tags,
                "ent_subjects": list(metadata.get("ent_subjects", []) or []),
                "min_score": metadata.get("min_score"),
                "text": text_value,
            }
        )
    if not candidate_programs:
        candidate_programs = [
            {
                "name": program.name,
                "tags": list(program.tags),
                "ent_subjects": list(program.ent_subjects),
                "min_score": program.min_score,
                "text": program.description,
            }
            for program in get_programs()
        ]

    for program in candidate_programs:
        subject_source = " ".join(program["tags"] + program["ent_subjects"]).lower()
        subject_match = 1.0 if profile_subjects and any(token in subject_source for token in profile_subjects.split()) else 0.0
        interest_match = 1.0 if profile_activity and any(token in " ".join(program["tags"]).lower() for token in profile_activity.split()) else 0.0
        career_tokens = [token for token in simple_words(career_goal) if len(token) > 2]
        career_match = 1.0 if career_goal and any(token in (program["name"] + " " + " ".join(program["tags"])).lower() for token in career_tokens) else 0.0
        if ent_score is not None and program["min_score"]:
            ent_match = 1.0 if ent_score >= program["min_score"] else max(0.0, ent_score / max(program["min_score"], 1))
        else:
            ent_match = 0.5

        program_embedding = embed_model.encode(
            [f"passage: {program['name']}. {' '.join(program['tags'])} {' '.join(program['ent_subjects'])} {program['text']}"],
            convert_to_numpy=True,
            normalize_embeddings=True,
        )[0]
        semantic_match = float(np.dot(profile_embedding, program_embedding))
        total = 0.30 * subject_match + 0.25 * interest_match + 0.20 * career_match + 0.15 * ent_match + 0.10 * max(0.0, semantic_match)

        reasons = []
        if subject_match:
            reasons.append("профильные предметы совпадают")
        if interest_match:
            reasons.append("интересы хорошо пересекаются")
        if career_match:
            reasons.append("подходит по карьерной цели")
        if ent_score is not None and program["min_score"]:
            reasons.append("подходит по условиям поступления")

        scoring_table.append(
            {
                "program": program,
                "score": round(total, 4),
                "reasons": reasons or ["есть частичное совпадение профиля"],
            }
        )

    scoring_table.sort(key=lambda item: item["score"], reverse=True)
    recommendations = [
        {
            "program": item["program"]["name"],
            "reasons": item["reasons"][:3],
        }
        for item in scoring_table[:3]
    ]
    return scoring_table, recommendations
