from __future__ import annotations

from typing import Any

from core.knowledge_assets import get_programs


_DEFAULT_INTERVIEW_FIELDS = [
    "favorite_subjects",
    "activity_type",
    "career_goal",
    "ent_score",
    "language",
    "budget",
]
_DEFAULT_KNOWN_SUBJECTS = [
    "математика",
    "информатика",
    "физика",
    "химия",
    "биология",
    "русский язык",
    "казахский язык",
    "английский язык",
    "история",
    "география",
]
_DEFAULT_INTERVIEW_FIELD_HINTS = {
    "favorite_subjects": {
        "anchor": "school subjects and academic strengths",
        "query": "образовательные программы профильные предметы ЕНТ специальности",
        "ru": "Уточни учебные предметы и академические сильные стороны абитуриента, чтобы связать их с программами из базы.",
        "en": "Clarify the applicant's school subjects and academic strengths to connect them with programs from the knowledge base.",
        "kk": "Базадағы бағдарламалармен байланыстыру үшін талапкердің пәндері мен оқу жағынан күшті тұстарын нақтыла.",
    },
    "activity_type": {
        "anchor": "preferred study and work format",
        "query": "образовательные программы направления навыки деятельность практика лаборатория проект",
        "ru": "Уточни предпочитаемый формат деятельности, чтобы сузить направления обучения по базе.",
        "en": "Clarify the preferred activity format to narrow the study directions using the knowledge base.",
        "kk": "База бойынша оқу бағыттарын тарылту үшін ұнайтын жұмыс не оқу форматын нақтыла.",
    },
    "career_goal": {
        "anchor": "target field and professional direction",
        "query": "образовательные программы кем работать карьерные траектории направления подготовки",
        "ru": "Уточни желаемую сферу или профессиональное направление, чтобы подобрать релевантные программы из базы.",
        "en": "Clarify the desired field or professional direction to match relevant programs from the knowledge base.",
        "kk": "Базадан сәйкес бағдарламаларды табу үшін қалаған сала не кәсіби бағытты нақтыла.",
    },
    "ent_score": {
        "anchor": "admission competitiveness and score range",
        "query": "ЕНТ проходные баллы образовательные программы конкурс грант",
        "ru": "Уточни балл ЕНТ, чтобы соотнести абитуриента с проходными и конкурсными данными из базы.",
        "en": "Clarify the UNT score to compare the applicant with passing-score and competition data from the knowledge base.",
        "kk": "Талапкерді базадағы өту балы және конкурс деректерімен салыстыру үшін ҰБТ нәтижесін нақтыла.",
    },
    "language": {
        "anchor": "language of study",
        "query": "язык обучения образовательные программы русский казахский",
        "ru": "Уточни предпочитаемый язык обучения, если это важно для выбора программ и условий из базы.",
        "en": "Clarify the preferred language of study if it matters for selecting programs and conditions from the knowledge base.",
        "kk": "Бағдарламалар мен талаптарды база бойынша іріктеу үшін қажет болса оқу тілін нақтыла.",
    },
    "budget": {
        "anchor": "grant or paid study preference",
        "query": "грант платное обучение стоимость образовательные программы",
        "ru": "Уточни финансовый режим: грант, платное обучение или оба варианта, чтобы опереться на условия из базы.",
        "en": "Clarify the financial mode: grant, paid study, or both, so the answer can use conditions from the knowledge base.",
        "kk": "Базадағы шарттарға сүйену үшін грант, ақылы оқу немесе екеуін де қарастыратынын нақтыла.",
    },
}

INTERVIEW_FIELDS = list(_DEFAULT_INTERVIEW_FIELDS)
KNOWN_SUBJECTS = list(_DEFAULT_KNOWN_SUBJECTS)
INTERVIEW_FIELD_HINTS = dict(_DEFAULT_INTERVIEW_FIELD_HINTS)


def _top_program_examples(programs, limit: int = 6) -> list[str]:
    names: list[str] = []
    for program in programs:
        name = str(getattr(program, "name", "") or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _top_subject_examples(programs, limit: int = 10) -> list[str]:
    subjects: list[str] = []
    for program in programs:
        for subject in getattr(program, "ent_subjects", []) or []:
            cleaned = str(subject or "").strip().lower()
            if cleaned and cleaned not in subjects:
                subjects.append(cleaned)
            if len(subjects) >= limit:
                return subjects
    return subjects


def _top_tag_examples(programs, limit: int = 12) -> list[str]:
    tags: list[str] = []
    banned = {"образовательные", "программы", "программа", "университет", "грант", "ент"}
    for program in programs:
        for tag in getattr(program, "tags", []) or []:
            cleaned = str(tag or "").strip().lower()
            if not cleaned or len(cleaned) < 4 or cleaned in banned or cleaned.isdigit():
                continue
            if cleaned not in tags:
                tags.append(cleaned)
            if len(tags) >= limit:
                return tags
    return tags


def _build_interview_metadata(programs) -> tuple[list[str], list[str], dict[str, dict[str, str]]]:
    fields = list(_DEFAULT_INTERVIEW_FIELDS)
    program_examples = _top_program_examples(programs)
    subject_examples = _top_subject_examples(programs)
    tag_examples = _top_tag_examples(programs)

    base_query = " ".join(program_examples[:4] + subject_examples[:4] + tag_examples[:6]).strip()
    subjects_query = " ".join(subject_examples[:6] + program_examples[:3]).strip() or base_query
    activity_query = " ".join(tag_examples[:8] + program_examples[:3]).strip() or base_query
    career_query = " ".join(program_examples[:5] + tag_examples[:6]).strip() or base_query

    hints: dict[str, dict[str, str]] = {
        "favorite_subjects": {
            "anchor": "school subjects and academic strengths grounded in the current program catalog",
            "query": subjects_query,
            "ru": f"Уточни предметы абитуриента, чтобы сопоставить их с текущими программами и профильными предметами из базы: {', '.join(subject_examples[:4]) or 'данные из каталога программ'}.",
            "en": f"Clarify the applicant's school subjects so they can be matched with the current program catalog and subject requirements from the knowledge base: {', '.join(subject_examples[:4]) or 'current catalog data'}.",
            "kk": f"Талапкердің пәндерін нақтыла, сонда оларды ағымдағы бағдарлама каталогы мен база деректерімен сәйкестендіруге болады: {', '.join(subject_examples[:4]) or 'каталог деректері'}.",
        },
        "activity_type": {
            "anchor": "preferred study format and type of program activity derived from the current catalog",
            "query": activity_query,
            "ru": f"Уточни, какой формат учебной деятельности ближе абитуриенту, опираясь на направления из базы: {', '.join(tag_examples[:5]) or 'направления из каталога'}.",
            "en": f"Clarify which type of study activity fits the applicant better, using current directions from the knowledge base: {', '.join(tag_examples[:5]) or 'catalog directions'}.",
            "kk": f"Базадағы қазіргі бағыттарға сүйеніп, талапкерге қай оқу форматы жақын екенін нақтыла: {', '.join(tag_examples[:5]) or 'каталог бағыттары'}.",
        },
        "career_goal": {
            "anchor": "target field connected to actual programs and career tracks from the knowledge base",
            "query": career_query,
            "ru": f"Уточни желаемое направление или сферу, чтобы связать абитуриента с реальными программами из базы: {', '.join(program_examples[:4]) or 'текущие программы'}.",
            "en": f"Clarify the desired direction or field to connect the applicant with real programs from the knowledge base: {', '.join(program_examples[:4]) or 'current programs'}.",
            "kk": f"Талапкерді базадағы нақты бағдарламалармен байланыстыру үшін қалаған бағыт не саланы нақтыла: {', '.join(program_examples[:4]) or 'қазіргі бағдарламалар'}.",
        },
        "ent_score": {
            "anchor": "score range needed to compare the applicant with current passing-score data",
            "query": f"{subjects_query} ЕНТ проходной балл конкурс",
            "ru": "Уточни балл ЕНТ, чтобы сравнить его с актуальными порогами и конкурсными данными из базы.",
            "en": "Clarify the UNT score so it can be compared with the current passing-score and competition data from the knowledge base.",
            "kk": "ҰБТ нәтижесін нақтыла, сонда оны базадағы өзекті өту балы және конкурс деректерімен салыстыруға болады.",
        },
        "language": {
            "anchor": "study language preference relevant to the current knowledge base and program set",
            "query": f"{base_query} язык обучения русский казахский",
            "ru": "Уточни предпочтительный язык обучения, если он влияет на подбор вариантов из текущей базы.",
            "en": "Clarify the preferred language of study if it affects the available options in the current knowledge base.",
            "kk": "Егер оқу тілі ағымдағы база бойынша қолжетімді нұсқаларға әсер етсе, оны нақтыла.",
        },
        "budget": {
            "anchor": "financial preference aligned with current grant and paid-study information from the knowledge base",
            "query": f"{base_query} грант стоимость платное обучение",
            "ru": "Уточни финансовый режим: грант, платное обучение или оба варианта, чтобы опереться на актуальные условия из базы.",
            "en": "Clarify the financial mode: grant, paid study, or both, so the answer can use the current conditions from the knowledge base.",
            "kk": "Ағымдағы база шарттарына сүйену үшін грант, ақылы оқу немесе екеуін де қарастыратынын нақтыла.",
        },
    }
    return fields, subject_examples or list(_DEFAULT_KNOWN_SUBJECTS), hints or dict(_DEFAULT_INTERVIEW_FIELD_HINTS)


def refresh_interview_metadata() -> None:
    global INTERVIEW_FIELDS, KNOWN_SUBJECTS, INTERVIEW_FIELD_HINTS
    fields, subjects, hints = _build_interview_metadata(get_programs())
    INTERVIEW_FIELDS = fields
    KNOWN_SUBJECTS = subjects or list(_DEFAULT_KNOWN_SUBJECTS)
    INTERVIEW_FIELD_HINTS = hints or dict(_DEFAULT_INTERVIEW_FIELD_HINTS)


def field_prompt(field: str, lang: str) -> str:
    field_meta = INTERVIEW_FIELD_HINTS.get(field, INTERVIEW_FIELD_HINTS["career_goal"])
    return field_meta.get(lang, field_meta["ru"])


def field_anchor(field: str) -> dict[str, str]:
    return INTERVIEW_FIELD_HINTS.get(field, INTERVIEW_FIELD_HINTS["career_goal"])


def field_question_seed(field: str, lang: str) -> str:
    field_meta = field_anchor(field)
    goal = field_meta.get(lang, field_meta.get("ru", field))
    anchor = field_meta.get("anchor", field)
    if lang == "en":
        return f"Please clarify this so I can match it with the university knowledge base: {anchor}."
    if lang == "kk":
        return f"Университет базасымен сәйкестендіру үшін мынаны нақтылаңыз: {goal}."
    return f"Чтобы сопоставить это с базой университета, уточните, пожалуйста: {goal}"


def next_missing_field(profile: dict[str, Any]) -> tuple[str, str] | None:
    for field in INTERVIEW_FIELDS:
        value = profile.get(field)
        if not value:
            return field, field_question_seed(field, "ru")
    return None


def next_interview_question(profile: dict[str, Any]) -> str | None:
    missing = next_missing_field(profile)
    return missing[1] if missing else None


def profile_is_complete(profile: dict[str, Any]) -> bool:
    return all([
        profile.get("favorite_subjects"),
        profile.get("activity_type"),
        profile.get("career_goal"),
        profile.get("ent_score"),
        profile.get("language"),
        profile.get("budget"),
    ])


refresh_interview_metadata()
