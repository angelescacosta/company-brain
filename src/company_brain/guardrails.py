"""The output gate (T25): no unchecked text reaches the user.

Every mode and the export call `gate()`. The model's output is structured
(`facts`, each citing sources; `suggestions`; `questions`), and an item is
routed by its field only, never by its wording.

Facts: citations must be `[n]` to a retrieved source; every value in the
grammar below must appear in a cited source after normalisation, or it is
replaced by "[not in sources]" with a warning. Values outside the grammar
(written-out numbers, relative dates, ambiguous numeric dates) are marked
"unchecked". Names are checked best effort against the cited sources and an
allowlist, and each fact gets a claim score (HHEM); both only warn.
Suggestions go in a box headed "Suggestions, not agreed", values marked
"proposed". Questions may not carry values.

Value grammar: digit-based numbers with "." or "," separators; amounts with €,
EUR, euro(s), $ or USD; percentages; dates as ISO, numeric day/month/year and
month names in English and Spanish.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from company_brain.normalise import MONTHS, AmbiguousDate, parse_date

if TYPE_CHECKING:
    from company_brain.answer import Answer

NOT_IN_SOURCES = "[not in sources]"
VALUE_REMOVED = "[value removed]"
SUGGESTIONS_HEADING = "Suggestions, not agreed"
CLAIM_FLOOR = 0.5
UNREADABLE = "The answer could not be checked, so it is not shown. Please ask again."

_NUMBER = r"\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?"
_MONTH = "|".join(
    sorted(
        {m for names in MONTHS.values() for m in names} | {m[:3] for m in MONTHS["en"]},
        key=len,
        reverse=True,
    )
)
_DATE = re.compile(
    rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b"
    rf"|\b\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{4}}\b"
    rf"|\b\d{{1,2}}(?:\s+de)?\s+(?:{_MONTH})\.?(?:\s+de)?\s+\d{{4}}\b"
    rf"|\b(?:{_MONTH})\.?\s+\d{{1,2}},\s+\d{{4}}\b",
    re.IGNORECASE,
)
_AMOUNT = re.compile(
    rf"(?:€|\$|\bEUR|\bUSD)\s?(?:{_NUMBER})|(?:{_NUMBER})\s?(?:€|\$|EUR\b|USD\b|euros?\b)",
    re.IGNORECASE,
)
_PERCENT = re.compile(rf"(?:{_NUMBER})\s?%")
_PLAIN = re.compile(rf"(?<![\w.,]){_NUMBER}(?![\w])")
_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")
_LIST_NUMBER = re.compile(r"(?m)^\s*\d+[.)]\s+")
_UNITS = (
    r"days?|weeks?|months?|years?|euros?|percent|per cent|"
    r"d[ií]as?|semanas?|mes(?:es)?|años?|por ciento"
)
_NUMBER_WORDS = (
    r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|uno|una|dos|tres|cuatro|"
    r"cinco|seis|siete|ocho|nueve|diez|once|doce|quince|veinte|treinta|cuarenta|cincuenta|"
    r"sesenta|setenta|ochenta|noventa|cien|cientos?|mil|mill[oó]n|millones"
)
_WRITTEN = re.compile(
    rf"\b(?:(?:{_NUMBER_WORDS})(?:[\s-]+(?:y\s+|and\s+)?(?:{_NUMBER_WORDS}))*)"
    rf"(?:\s+(?:{_UNITS}))?\b",
    re.IGNORECASE,
)
_SCALE = re.compile(r"hundred|thousand|million|\bmil\b|mill[oó]n|millones|\bcien", re.IGNORECASE)
_WEEKDAYS = (
    "monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    "lunes|martes|miércoles|jueves|viernes|sábado|domingo"
)
_RELATIVE = re.compile(
    rf"\b(?:the day after tomorrow|today|tomorrow|yesterday|"
    rf"(?:next|last|this)\s+(?:week|month|year|{_WEEKDAYS})|"
    rf"pasado mañana|hoy|mañana|ayer|la semana (?:que viene|pasada|próxima)|"
    rf"la próxima semana|el (?:mes|año) (?:que viene|pasado|próximo)|"
    rf"el próximo (?:mes|año|{_WEEKDAYS}))\b",
    re.IGNORECASE,
)
_ADDRESSED = re.compile(
    r"\b(?:AI|IA|assistant|asistente|chatbot|language model|modelo de lenguaje)\b"
    r"|\bsystem prompt\b|\bignore (?:all |any )?(?:previous|prior|the)\b"
    r"|\b(?:ignora|olvida)\b|\bsystem\s*:",
    re.IGNORECASE,
)
_NAME = re.compile(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+\b")


@dataclass(frozen=True)
class Value:
    kind: str  # amount, percent, number, date
    raw: str
    start: int
    end: int
    currency: str | None = None
    numbers: frozenset[Decimal] = frozenset()
    days: frozenset[date] = frozenset()


@dataclass
class FactCheck:
    text: str
    passed_values: list[str] = field(default_factory=list)
    failed_values: list[str] = field(default_factory=list)
    unchecked: list[str] = field(default_factory=list)
    citation_ok: bool = True


@dataclass
class Gated:
    text: str
    facts_text: str = ""
    suggestions_text: str = ""
    questions_text: str = ""
    warnings: list[str] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)  # the cited ones, for display


def addresses_the_assistant(text: str) -> bool:
    """True for a line that speaks to an AI or assistant (a likely planted instruction)."""
    return bool(_ADDRESSED.search(text))


def describe_location(span: dict[str, Any]) -> str:
    """A source location in plain words, for the sources shown under an answer."""
    parts = []
    if span.get("image"):
        parts.append("image")
    if "clause" in span:
        parts.append(f"clause {span['clause']}")
    for key, word in (
        ("page", "page"),
        ("slide", "slide"),
        ("paragraph", "paragraph"),
        ("line", "line"),
        ("row", "row"),
    ):
        if key in span:
            parts.append(f"{word} {span[key]}")
    if "cell" in span:
        parts.append(f"cells {span['cell']}")
    if "start" in span:
        parts.append(f"at {str(span['start']).split('.')[0]}")
    return ", ".join(parts)


def render_citations(numbers: Iterable[int]) -> str:
    return " ".join(f"[{n}]" for n in numbers)


def parse_citations(text: str) -> list[int]:
    return [int(n) for group in _CITATION.findall(text) for n in group.split(",")]


def _clean(text: str) -> str:
    """Citation markers and list numbers are blanked (same length), so spans stay valid."""
    text = _CITATION.sub(lambda m: " " * len(m.group(0)), text)
    return _LIST_NUMBER.sub(lambda m: " " * len(m.group(0)), text)


def interpretations(token: str) -> set[Decimal]:
    """A number read with English and with Spanish separators, where the format allows it."""
    out: set[Decimal] = set()
    for thousands, point in ((",", "."), (".", ",")):
        t, p = re.escape(thousands), re.escape(point)
        if re.fullmatch(rf"\d{{1,3}}(?:{t}\d{{3}})+(?:{p}\d+)?|\d+(?:{p}\d+)?", token):
            try:
                out.add(Decimal(token.replace(thousands, "").replace(point, ".")).normalize())
            except InvalidOperation:
                continue
    return out


def _days(raw: str, language: str, source: bool) -> frozenset[date] | None:
    """Possible dates; None when an answer's numeric date is ambiguous (it is unchecked)."""
    locales = ["es-ES"] if language == "es" else ["en-GB"]
    found: set[date] = set()
    try:
        parsed = parse_date(raw, None)
        if parsed:
            found.add(parsed)
    except AmbiguousDate:
        if not source:
            return None
        found |= {d for d in (parse_date(raw, "en-GB"), parse_date(raw, "en-US")) if d}
    if not found:
        found |= {d for loc in locales if (d := parse_date(raw.replace(".", ""), loc))}
    return frozenset(found)


def values(text: str, language: str = "en", source: bool = False) -> list[Value]:
    cleaned = _clean(text)
    taken: list[tuple[int, int]] = []
    found: list[Value] = []

    def free(start: int, end: int) -> bool:
        return all(end <= a or start >= b for a, b in taken)

    for match in _DATE.finditer(cleaned):
        days = _days(match.group(0), language, source)
        taken.append(match.span())
        if days:
            found.append(Value("date", match.group(0), *match.span(), days=days))
            if source:  # a year written inside a date also counts as a number in a source
                for day in days:
                    found.append(
                        Value(
                            "number",
                            str(day.year),
                            *match.span(),
                            numbers=frozenset({Decimal(day.year)}),
                        )
                    )
    for kind, pattern in (("amount", _AMOUNT), ("percent", _PERCENT), ("number", _PLAIN)):
        for match in pattern.finditer(cleaned):
            if not free(*match.span()):
                continue
            raw = match.group(0)
            token = re.search(_NUMBER, raw)
            if token is None:
                continue
            currency = None
            if kind == "amount":
                currency = "USD" if re.search(r"\$|USD", raw, re.IGNORECASE) else "EUR"
            found.append(
                Value(
                    kind,
                    raw,
                    *match.span(),
                    currency=currency,
                    numbers=frozenset(interpretations(token.group(0))),
                )
            )
            taken.append(match.span())
    return sorted(found, key=lambda v: v.start)


def unchecked_phrases(text: str, language: str = "en") -> list[str]:
    cleaned = _clean(text)
    out = []
    for match in _WRITTEN.finditer(cleaned):
        phrase = match.group(0)
        if _SCALE.search(phrase) or re.search(rf"\b(?:{_UNITS})\b", phrase, re.IGNORECASE):
            out.append(phrase)
    out += [m.group(0) for m in _RELATIVE.finditer(cleaned)]
    for match in _DATE.finditer(cleaned):
        if _days(match.group(0), language, source=False) is None:
            out.append(match.group(0))
    return out


def _matches(value: Value, pool: Sequence[Value]) -> bool:
    for other in pool:
        if value.kind == "date" or other.kind == "date":
            if value.kind == other.kind == "date" and value.days & other.days:
                return True
            continue
        if value.kind == "amount" and (other.kind != "amount" or value.currency != other.currency):
            continue  # money matches money in the same currency, never a bare number
        if value.kind == "percent" and other.kind == "amount":
            continue
        if value.numbers & other.numbers:
            return True
    return False


def matches_any(value: Value, pool: Sequence[Value]) -> bool:
    """The value appears in the pool under the grammar's matching rules (used by T34)."""
    return _matches(value, pool)


def check_fact(
    text: str, citations: Sequence[int], sources: dict[int, str], language: str = "en"
) -> FactCheck:
    if not citations or not set(citations) <= set(sources):
        return FactCheck(NOT_IN_SOURCES, citation_ok=False)
    pool = [v for n in citations for v in values(sources[n], language, source=True)]
    result = FactCheck(text)
    out = text
    for value in reversed(values(text, language)):
        if _matches(value, pool):
            result.passed_values.append(value.raw)
        else:
            result.failed_values.append(value.raw)
            out = out[: value.start] + NOT_IN_SOURCES + out[value.end :]
    for phrase in unchecked_phrases(out, language):
        result.unchecked.append(phrase)
        out = out.replace(phrase, f"{phrase} (unchecked)", 1)
    result.text = out
    return result


def _plain(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _mark(text: str, language: str, label: str) -> str:
    out = text
    for value in reversed(values(text, language)):
        out = out[: value.end] + f" ({label})" + out[value.end :]
    return out


def _strip_values(text: str, language: str) -> str:
    out = text
    for value in reversed(values(text, language)):
        out = out[: value.start] + VALUE_REMOVED + out[value.end :]
    return out


def gate(
    answer: Answer,
    claim_score: Callable[[str, str], float] | None = None,
    allowlist: Iterable[str] = (),
) -> Gated:
    if answer.not_found:
        return Gated(text=answer.text)
    if not answer.readable:
        return Gated(text=UNREADABLE, warnings=["the model's output was not readable"])
    language = answer.language
    sources = {s.number: f"{s.hit.label}\n{s.hit.text}" for s in answer.sources}
    allowed = {_plain(a) for a in allowlist}
    warnings: list[str] = []
    lines = []
    for fact in answer.facts:
        checked = check_fact(fact.text, list(fact.sources), sources, language)
        if not checked.citation_ok:
            warnings.append(f"a statement without a retrieved source was removed: {fact.text[:60]}")
            lines.append(NOT_IN_SOURCES)
            continue
        for value in checked.failed_values:
            warnings.append(f"{value} is not in the cited sources")
        cited = " ".join(sources[n] for n in fact.sources)
        for name in _NAME.findall(fact.text):
            if _plain(name) not in _plain(cited) and _plain(name) not in allowed:
                warnings.append(f"name not found in the cited sources: {name}")
        if claim_score is not None and claim_score(cited, fact.text) < CLAIM_FLOOR:
            warnings.append(f"not clearly supported by its sources: {fact.text[:60]}")
        lines.append(f"{checked.text} {render_citations(fact.sources)}".strip())
    facts_text = "\n".join(lines)
    box = ""
    if answer.suggestions:
        box = (
            SUGGESTIONS_HEADING
            + "\n"
            + "\n".join(f"- {_mark(s, language, 'proposed')}" for s in answer.suggestions)
        )
    questions_text = ""
    if answer.questions:
        cleaned = []
        for question in answer.questions:
            stripped = _strip_values(question, language)
            if stripped != question:
                warnings.append("a value was removed from a question")
            cleaned.append(f"- {stripped}")
        questions_text = "Questions:\n" + "\n".join(cleaned)
    parts = [facts_text, box, questions_text]
    if warnings:
        parts.append("Warnings:\n" + "\n".join(f"- {w}" for w in warnings))
    text = "\n\n".join(p for p in parts if p)
    cited_numbers = set(parse_citations(text))
    shown = [
        {
            "n": s.number,
            "file": s.hit.rel_path,
            "name": PurePosixPath(s.hit.rel_path).name,
            "location": "; ".join(d for d in (describe_location(x) for x in s.hit.spans[:2]) if d),
            "excerpt": s.hit.text[:400],
        }
        for s in answer.sources
        if s.number in cited_numbers
    ]
    return Gated(
        text=text,
        facts_text=facts_text,
        suggestions_text=box,
        questions_text=questions_text,
        warnings=warnings,
        sources=shown,
    )


def hhem_scorer() -> Callable[[str, str], float]:
    """HHEM-2.1-Open at its pinned revision (T3), offline."""
    import os
    import tomllib
    from pathlib import Path

    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from transformers import AutoModelForSequenceClassification

    models = tomllib.loads(
        (Path(__file__).resolve().parents[2] / "config" / "models.toml").read_text()
    )
    name = "vectara/hallucination_evaluation_model"
    model = AutoModelForSequenceClassification.from_pretrained(
        name, revision=models["huggingface"][name]["revision"], trust_remote_code=True
    )

    def score(premise: str, hypothesis: str) -> float:
        return float(model.predict([(premise, hypothesis)]).tolist()[0])

    return score
