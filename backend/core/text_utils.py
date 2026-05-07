from __future__ import annotations

import json
from typing import Any


def normalize_spaces(text: Any) -> str:
    return " ".join(str(text or "").split())


def extract_json_text(raw: str) -> str:
    text = str(raw or "")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start : end + 1]


def extract_json_object(text: str) -> dict[str, Any]:
    if not text:
        return {}
    payload_text = extract_json_text(text)
    if not payload_text:
        return {}
    try:
        data = json.loads(payload_text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def extract_one_of(text: str, options: list[str]) -> str | None:
    lowered = str(text or "").lower()
    for option in sorted(options, key=len, reverse=True):
        if option in lowered:
            return option
    return None


def simple_words(text: str) -> list[str]:
    words: list[str] = []
    current: list[str] = []
    for char in str(text or "").lower():
        if char.isalnum() or char == "_":
            current.append(char)
            continue
        if current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    return words


def digit_tokens(text: str) -> list[str]:
    return [token for token in simple_words(text) if token.isdigit()]


def contains_year(text: str) -> bool:
    return any(len(token) == 4 and token.startswith("20") for token in digit_tokens(text))


def contains_large_number(text: str, min_digits: int = 3) -> bool:
    return any(len(token) >= min_digits for token in digit_tokens(text))


def looks_like_phone(text: str) -> bool:
    digits = "".join(char for char in str(text or "") if char.isdigit())
    return len(digits) >= 7


def has_url_or_email(text: str) -> bool:
    lowered = str(text or "").lower()
    return "@" in lowered or "http://" in lowered or "https://" in lowered or "www." in lowered


def contains_numbered_list(text: str) -> bool:
    for raw_line in str(text or "").splitlines():
        line = raw_line.lstrip()
        if not line:
            continue
        if line.startswith(("-", "вЂў", "*")):
            return True
        digits: list[str] = []
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


def strip_leading_list_marker(text: str) -> str:
    line = str(text or "").lstrip()
    while line and line[0] in "-*вЂў":
        line = line[1:].lstrip()
    digits: list[str] = []
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


def cyrillic_word_count(text: str) -> int:
    return sum(1 for token in simple_words(text) if any("а" <= ch <= "я" or ch in "ёәіңғүұқөһ" for ch in token))


def latin_word_count(text: str) -> int:
    return sum(1 for token in simple_words(text) if any("a" <= ch <= "z" for ch in token))
