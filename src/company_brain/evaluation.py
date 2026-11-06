"""Evaluation metrics and report helpers (T34).

Retrieval (answerable questions only): a chunk covers a required span when it
contains at least half of the span's characters. Recall@5 is the share of
questions whose required spans are all covered by the top 5 chunks; evidence
coverage counts conflict counterparts and parent sections added after the top
5 too; MRR is the mean of 1 / rank of the first covering chunk (0 if none).

Answers: a typed fact is correct when its value appears in the answer within
the value grammar (any format), a decision when its normalised text does, and
an action when its normalised text and its owner do. Hard blockers fail a run
whatever the averages: any result from another client, and any value shown
without a source.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from company_brain import guardrails

COVER_SHARE = 0.5
SMALL_SAMPLE = 20


def _squash(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def covers(chunk: str, span: str) -> bool:
    """True when the chunk contains at least half of the span's characters (in order)."""
    span, chunk = _squash(span), _squash(chunk)
    if not span:
        return False
    if span in chunk:
        return True
    matcher = difflib.SequenceMatcher(None, span, chunk, autojunk=False)
    shared = sum(block.size for block in matcher.get_matching_blocks())
    return shared >= COVER_SHARE * len(span)


def _first_cover(spans: Sequence[str], ranked: Sequence[str]) -> int | None:
    for rank, chunk in enumerate(ranked, start=1):
        if any(covers(chunk, span) for span in spans):
            return rank
    return None


def recall_at_k(
    spans: Sequence[Sequence[str]], ranked: Sequence[Sequence[str]], k: int = 5
) -> float:
    if not spans:
        return 0.0
    hits = sum(
        all(any(covers(chunk, span) for chunk in chunks[:k]) for span in required)
        for required, chunks in zip(spans, ranked, strict=True)
    )
    return hits / len(spans)


def evidence_coverage(
    spans: Sequence[Sequence[str]],
    top: Sequence[Sequence[str]],
    added: Sequence[Sequence[str]],
) -> float:
    if not spans:
        return 0.0
    hits = sum(
        all(any(covers(chunk, span) for chunk in [*base[:5], *extra]) for span in required)
        for required, base, extra in zip(spans, top, added, strict=True)
    )
    return hits / len(spans)


def mrr(spans: Sequence[Sequence[str]], ranked: Sequence[Sequence[str]]) -> float:
    if not spans:
        return 0.0
    total = 0.0
    for required, chunks in zip(spans, ranked, strict=True):
        rank = _first_cover(required, chunks)
        total += 0.0 if rank is None else 1.0 / rank
    return total / len(spans)


def percent(count: int, total: int) -> str:
    """A percentage always shown with its count; small denominators are flagged."""
    if total == 0:
        return "n/a (0 of 0)"
    note = ", small sample" if total < SMALL_SAMPLE else ""
    return f"{round(100 * count / total)}% ({count} of {total}{note})"


def _key(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", text).split())


def _contains(answer: str, phrase: str) -> bool:
    want, got = _key(phrase), _key(answer)
    if want in got:
        return True
    words = got.split()
    size = len(want.split())
    for start in range(max(1, len(words) - size + 1)):
        window = " ".join(words[start : start + size + 2])
        if difflib.SequenceMatcher(None, want, window).ratio() >= 0.85:
            return True
    return False


def _words_present(answer: str, phrase: str, share: float = 0.8) -> bool:
    """Most of the phrase's content words appear in the answer (allows rewording)."""
    words = [w for w in _key(phrase).split() if len(w) > 3]
    if not words:
        return False
    present = set(_key(answer).split())
    return sum(w in present for w in words) >= share * len(words)


def _values_present(expected_text: str, answer: str, language: str) -> bool:
    expected = guardrails.values(expected_text, language)
    if not expected:
        return False
    shown = guardrails.values(answer, language)
    return all(guardrails.matches_any(value, shown) for value in expected)


def fact_correct(fact: dict[str, Any], answer: str, language: str, mode: str = "answer") -> bool:
    """Is the required fact in the answer?

    Values (amounts, dates, numbers, receipt fields) must appear within the grammar, in any
    format. Decisions may be reworded. In Answer mode an action counts when its owner and due
    date appear (the question may be in another language than the call); in a summary the
    action's own words and its owner must appear.
    """
    kind = fact.get("type")
    if kind == "decision":
        text = str(fact["decision"])
        return _contains(answer, text) or (
            _words_present(answer, text) and _values_present(text, answer, language)
            if guardrails.values(text, language)
            else _words_present(answer, text)
        )
    if kind == "action":
        owner_ok = _key(str(fact["owner"])) in _key(answer)
        if mode == "answer" and fact.get("due"):
            return owner_ok and _values_present(str(fact["due"]), answer, language)
        return owner_ok and _contains(answer, str(fact["action"]))
    if kind == "receipt":
        fields = fact.get("fields", {})
        wanted = " ".join(str(fields[k]) for k in ("total", "vat") if fields.get(k))
        return _values_present(wanted, answer, language)
    if kind == "chart":
        return _values_present(str(fact.get("value")), answer, language)
    expected_text = str(fact.get("text") or fact.get("value", ""))
    if guardrails.values(expected_text, language):
        return _values_present(expected_text, answer, language)
    return _contains(answer, expected_text)


@dataclass
class QuestionResult:
    question_id: str
    language: str
    category: str
    passed: bool
    score: float
    retrieved_ok: bool
    leaks: list[str] = field(default_factory=list)
    unsourced_shown: list[str] = field(default_factory=list)


def blockers(results: Sequence[QuestionResult]) -> list[str]:
    """Release blockers found in a run; any one fails it, whatever the averages."""
    found = []
    for result in results:
        found += [f"{result.question_id}: client leak ({leak})" for leak in result.leaks]
        found += [
            f"{result.question_id}: value shown without a source ({value})"
            for value in result.unsourced_shown
        ]
    return found
