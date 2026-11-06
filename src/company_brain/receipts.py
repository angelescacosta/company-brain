"""Receipt and whiteboard fields from recognised text, and their scoring (T17.AC6, AC8).

Every field is compared exactly after normalisation (normalise.py); a field
that cannot be found is reported as missing with the text it was looked for
in, never guessed.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from company_brain.normalise import parse_amount, parse_date

_AMOUNT = re.compile(r"\d[\d.,]*\d|\d")
_DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}"
    r"|\d{1,2}(?: de)? [A-Za-zé]{3,10}\.?(?: de)? \d{4}"
)


@dataclass(frozen=True)
class Found:
    value: str | None  # normalised value, or None when missing
    raw: str  # the recognised line it came from ("" when nothing matched)


def plain(text: str) -> str:
    """Casefolded, accents kept, whitespace collapsed (for exact comparison of names)."""
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def lines_of(text: str) -> list[str]:
    return [" ".join(line.replace("|", " ").split()) for line in text.splitlines() if line.strip()]


def _last_amount(line: str, locale: str) -> str | None:
    for token in reversed(_AMOUNT.findall(line)):
        value = parse_amount(token, locale)
        if value is not None:
            return str(value)
    return None


def vendor(lines: list[str], expected: str) -> Found:
    target = plain(expected)
    for line in lines:
        if plain(line) == target:
            return Found(expected, line)
    close = difflib.get_close_matches(target, [plain(x) for x in lines], n=1, cutoff=0.5)
    raw = next((x for x in lines if close and plain(x) == close[0]), "")
    return Found(None, raw)


def receipt_date(lines: list[str], locale: str) -> Found:
    for line in lines:
        for token in _DATE.findall(line):
            parsed = parse_date(token, locale)
            if parsed is not None:
                return Found(parsed.isoformat(), line)
    return Found(None, "")


def labelled_amount(lines: list[str], labels: tuple[str, ...], locale: str) -> Found:
    for line in lines:
        words = plain(line)
        if any(label in words for label in labels) and "subtotal" not in words:
            value = _last_amount(line, locale)
            if value is not None:
                return Found(value, line)
    return Found(None, "")


def receipt_fields(text: str, language: str, expected_vendor: str) -> dict[str, Found]:
    locale = "en-GB" if language == "en" else "es-ES"
    lines = lines_of(text)
    total = labelled_amount(lines, ("total",), locale)
    currency = next(
        (
            Found(code, line)
            for line in lines
            for code in ("GBP", "EUR")
            if "TOTAL" in line.upper() and code in line
        ),
        Found(None, ""),
    )
    return {
        "vendor": vendor(lines, expected_vendor),
        "date": receipt_date(lines, locale),
        "total": total,
        "vat": labelled_amount(lines, ("vat", "iva"), locale),
        "currency": currency,
    }


def cord_total(text: str) -> Found:
    """CORD totals are whole rupiah; compare digits only (public_data.amount)."""
    for line in lines_of(text):
        words = plain(line)
        if "total" in words and "sub" not in words and "tax" not in words:
            digits = [re.sub(r"\D", "", t) for t in _AMOUNT.findall(line)]
            digits = [d for d in digits if len(d) >= 3]
            if digits:
                return Found(digits[-1], line)
    return Found(None, "")


def whiteboard_item(text: str, owner: str, due: date) -> tuple[Found, Found, Found]:
    """(action text, owner, due date) from the line carrying this item's due date.

    The line is found by its date, so a misread owner name is reported as a miss
    for that field only, with the line it was read from.
    """
    for line in lines_of(text):
        dates = [parse_date(t, "en-GB") for t in _DATE.findall(line)]
        if due not in dates:
            continue
        parts = [part.strip() for part in line.split(" - ")]
        action = re.sub(r"^\s*\d+[.)]\s*", "", parts[0]).strip()
        named = owner if any(plain(owner) == plain(part) for part in parts) else None
        return Found(action, line), Found(named, line), Found(due.isoformat(), line)
    return Found(None, ""), Found(None, ""), Found(None, "")


def write_ocr_report(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write the OCR results (T17, T11P) and rebuild the results dashboard (T34D)."""
    import csv

    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    from company_brain import dashboard

    dashboard.rebuild_after("ocr")
