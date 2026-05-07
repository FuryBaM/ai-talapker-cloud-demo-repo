import ast
import operator
import re
from decimal import Decimal, DivisionByZero, InvalidOperation


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_NUMBER_UNITS = {
    "тыс": Decimal("1000"),
    "тысяч": Decimal("1000"),
    "тысяча": Decimal("1000"),
    "тысячи": Decimal("1000"),
    "к": Decimal("1000"),
    "млн": Decimal("1000000"),
    "миллион": Decimal("1000000"),
    "миллиона": Decimal("1000000"),
    "миллионов": Decimal("1000000"),
    "млрд": Decimal("1000000000"),
    "миллиард": Decimal("1000000000"),
    "миллиарда": Decimal("1000000000"),
    "миллиардов": Decimal("1000000000"),
}


class CalculatorError(ValueError):
    pass


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == normalized.to_integral():
        return str(normalized.quantize(Decimal("1")))
    return format(normalized, "f").rstrip("0").rstrip(".")


def _normalize_expression(text: str) -> str:
    expr = text.strip()
    expr = expr.replace("^", "**")
    expr = expr.replace("×", "*").replace("÷", "/")
    expr = expr.replace(",", ".")
    expr = re.sub(r"(?i)\bсколько будет\b", "", expr)
    expr = re.sub(r"(?i)\bпосчитай(?:те)?\b", "", expr)
    expr = re.sub(r"(?i)\bвычисли(?:те)?\b", "", expr)
    expr = re.sub(r"(?i)\bреши\b", "", expr)
    expr = re.sub(r"(?i)\bравно\??", "", expr)
    expr = re.sub(r"\s+", " ", expr).strip(" ?=")
    return expr


def _extract_number_with_unit(text: str) -> Decimal | None:
    price_match = re.search(
        r"(?:цена|стоимость)[^\d]{0,20}(\d+(?:[\.,]\d+)?)\s*(тыс(?:яч[аи])?|к\b|млн|миллион(?:а|ов)?|млрд|миллиард(?:а|ов)?)?",
        text,
        flags=re.IGNORECASE,
    )
    if price_match:
        number = Decimal(price_match.group(1).replace(",", "."))
        unit = (price_match.group(2) or "").lower().strip()
        multiplier = _NUMBER_UNITS.get(unit, Decimal("1"))
        return number * multiplier

    candidates: list[tuple[Decimal, bool]] = []
    for match in re.finditer(
        r"(\d+(?:[\.,]\d+)?)\s*(тыс(?:яч[аи])?|к\b|млн|миллион(?:а|ов)?|млрд|миллиард(?:а|ов)?)?",
        text,
        flags=re.IGNORECASE,
    ):
        prefix = text[max(0, match.start() - 5): match.start()].lower()
        tail = text[match.end(): match.end() + 2]
        full_tail = text[match.end(): match.end() + 8].lower()
        if "%" in tail or prefix.endswith("за ") or "год" in full_tail or "лет" in full_tail:
            continue
        number = Decimal(match.group(1).replace(",", "."))
        unit = (match.group(2) or "").lower().strip()
        multiplier = _NUMBER_UNITS.get(unit, Decimal("1"))
        candidates.append((number * multiplier, bool(unit)))

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[1], item[0]), reverse=True)
    return candidates[0][0]


def _extract_percent(text: str) -> Decimal | None:
    match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:%|процент(?:а|ов)?)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return Decimal(match.group(1).replace(",", "."))


def _extract_years(text: str) -> Decimal | None:
    match = re.search(r"(?:за|на)\s+(\d+)\s*(?:год|года|лет)", text, flags=re.IGNORECASE)
    if not match:
        return None
    return Decimal(match.group(1))


def _looks_like_discount_question(text: str) -> bool:
    lower = text.lower()
    has_amount = _extract_number_with_unit(text) is not None
    has_percent = _extract_percent(text) is not None
    mentions_discount = any(token in lower for token in ["скидк", "процент", "%"])
    return has_amount and has_percent and mentions_discount


def _build_discount_expression(text: str) -> tuple[str, str] | None:
    if not _looks_like_discount_question(text):
        return None

    amount = _extract_number_with_unit(text)
    percent = _extract_percent(text)
    years = _extract_years(text) or Decimal("1")
    if amount is None or percent is None:
        return None

    total_price = amount * years
    discount = total_price * percent / Decimal("100")
    expression = f"{_format_decimal(amount)} * {_format_decimal(years)} * {_format_decimal(percent)} / 100"
    if years > 1:
        result = (
            f"скидка за {_format_decimal(years)} года(лет): {_format_decimal(discount)} "
            f"(от общей суммы {_format_decimal(total_price)})"
        )
    else:
        result = _format_decimal(discount)
    return expression, result


def looks_like_calculation(text: str) -> bool:
    lower = text.lower()
    calc_markers = ["посчитай", "вычисли", "сколько будет", "реши", "calculate", "what is"]
    if any(marker in lower for marker in calc_markers):
        return True
    if _looks_like_discount_question(text):
        return True
    if re.fullmatch(r"[\d\s\.,+\-*/()^:%=]+", text.strip()):
        return True
    return False


def _eval_node(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if type(node.op) is ast.Pow and abs(right) > 20:
            raise CalculatorError("Слишком большая степень для безопасного вычисления.")
        try:
            return _BIN_OPS[type(node.op)](left, right)
        except DivisionByZero as exc:
            raise CalculatorError("Деление на ноль невозможно.") from exc
    raise CalculatorError("Поддерживаются только арифметические выражения.")


def calculate(text: str) -> tuple[str, str]:
    discount_result = _build_discount_expression(text)
    if discount_result is not None:
        return discount_result

    expr = _normalize_expression(text)
    if not expr:
        raise CalculatorError("Не вижу выражения для вычисления.")
    try:
        tree = ast.parse(expr, mode="eval")
        value = _eval_node(tree)
    except (SyntaxError, InvalidOperation) as exc:
        raise CalculatorError("Не удалось разобрать выражение.") from exc

    return expr, _format_decimal(value)
