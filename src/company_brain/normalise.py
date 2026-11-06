"""Amounts and dates in recognised text, normalised for comparison with facts.json (T17).

Every parse takes an explicit locale. Without one, a numeric date whose day and
month could swap (03/04/2026) raises AmbiguousDate instead of being guessed.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

LOCALES = ("en-GB", "en-US", "es-ES")
MONTHS = {
    "en": [
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ],
    "es": [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ],
}  # fmt: skip
_CURRENCY = re.compile(r"(?i)(gbp|eur|usd|idr|rp\.?|[£€$])")


class AmbiguousDate(ValueError):
    """Day and month could be swapped and no locale says which is which."""


def _separators(locale: str) -> tuple[str, str]:
    """(thousands, decimal)"""
    return (".", ",") if locale == "es-ES" else (",", ".")


def render_amount(value: Decimal, locale: str, grouping: bool) -> str:
    text = f"{value:,.2f}" if grouping else f"{value:.2f}"
    if locale == "es-ES":
        text = text.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return text


def parse_amount(text: str, locale: str) -> Decimal | None:
    thousands, point = _separators(locale)
    body = _CURRENCY.sub("", text).strip()
    t, p = re.escape(thousands), re.escape(point)
    if not re.fullmatch(rf"\d{{1,3}}({t}\d{{3}})+({p}\d{{2}})?|\d+({p}\d{{2}})?", body):
        return None
    try:
        return Decimal(body.replace(thousands, "").replace(point, ".")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def render_date(day: date, locale: str, style: int) -> str:
    """One of the formats parse_date accepts for this locale (style picks which)."""
    language = locale[:2]
    month = MONTHS[language][day.month - 1]
    numeric = (
        f"{day.month:02d}/{day.day:02d}/{day.year}"
        if locale == "en-US"
        else f"{day.day:02d}/{day.month:02d}/{day.year}"
    )
    if locale == "en-US":
        named = [
            f"{month.title()} {day.day}, {day.year}",
            f"{month[:3].title()} {day.day}, {day.year}",
        ]
    elif language == "en":
        named = [
            f"{day.day} {month.title()} {day.year}",
            f"{day.day:02d} {month[:3].title()} {day.year}",
        ]
    else:
        named = [f"{day.day} de {month} de {day.year}"]
    styles = [day.isoformat(), numeric, numeric.replace("/", "-"), *named]
    return styles[style % len(styles)]


def _month(name: str, language: str) -> int | None:
    name = name.lower().rstrip(".")
    for number, full in enumerate(MONTHS[language], start=1):
        if name == full or (len(name) == 3 and full.startswith(name)):
            return number
    return None


def _make(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date(text: str, locale: str | None) -> date | None:
    text = text.strip()
    if match := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text):
        return _make(int(match[1]), int(match[2]), int(match[3]))
    if match := re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text):
        first, second, year = int(match[1]), int(match[2]), int(match[3])
        if locale is None:
            if first != second and first <= 12 and second <= 12:
                raise AmbiguousDate(text)
            day, month = (first, second) if first > 12 or second <= 12 else (second, first)
            return _make(year, month, day)
        if locale == "en-US":
            return _make(year, first, second)
        return _make(year, second, first)
    for language in ["en", "es"] if locale is None else [locale[:2]]:
        if (match := re.fullmatch(r"(\d{1,2}) de (\w+) de (\d{4})", text)) or (
            match := re.fullmatch(r"(\d{1,2}) (\w+\.?) (\d{4})", text)
        ):
            named = _month(match[2], language)
        elif match := re.fullmatch(r"(\w+\.?) (\d{1,2}), (\d{4})", text):
            number = _month(match[1], language)
            if number is not None:
                return _make(int(match[3]), number, int(match[2]))
            continue
        else:
            continue
        if named is not None:
            return _make(int(match[3]), named, int(match[1]))
    return None
