"""Reranker and relevance floor (T22).

The fused candidates are scored by the small multilingual cross-encoder (D31)
on the Apple GPU; the best five are kept, a conflict counterpart (draft and
signed version of the same agreement) is re-added when its partner is kept, and
"not found" is returned when there are no candidates or the best score is below
the language's floor (`config/relevance.toml`, tuned in T35).
"""

from __future__ import annotations

import math
import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from company_brain.retrieval import Hit

MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
KEEP = 5
REPO = Path(__file__).resolve().parents[2]
_SPANISH = re.compile(
    r"[¿¡ñ]|\b(el|la|los|las|de|del|qué|cuál|cuánto|cuántos|cuándo|quién|para|con|por|una?)\b",
    re.IGNORECASE,
)
_ENGLISH = re.compile(
    r"\b(the|what|which|who|when|how|is|are|of|for|with|did|does|and)\b", re.IGNORECASE
)


@dataclass(frozen=True)
class Selection:
    kept: tuple[tuple[Hit, float], ...]
    counterparts: tuple[tuple[Hit, float], ...] = ()
    not_found: bool = False


def pinned_revision(name: str) -> str:
    models = tomllib.loads((REPO / "config" / "models.toml").read_text(encoding="utf-8"))
    return str(models["huggingface"][name]["revision"])


def thresholds() -> dict[str, float]:
    data = tomllib.loads((REPO / "config" / "relevance.toml").read_text(encoding="utf-8"))
    return {language: float(value) for language, value in data["threshold"].items()}


def language_of(text: str) -> str:
    """'es' or 'en' from function words and Spanish punctuation (questions are short)."""
    return "es" if len(_SPANISH.findall(text)) > len(_ENGLISH.findall(text)) else "en"


def select(
    scored: Sequence[tuple[Hit, float]],
    threshold: float,
    counterparts: Mapping[str, set[str]] | None = None,
) -> Selection:
    if any(not math.isfinite(score) for _hit, score in scored):
        raise ValueError("a reranker score is not a finite number")
    ranked = sorted(scored, key=lambda pair: (-pair[1], pair[0].chunk_id))
    if not ranked or ranked[0][1] < threshold:
        return Selection(kept=(), not_found=True)
    kept = ranked[:KEEP]
    kept_docs = {hit.doc_id for hit, _score in kept}
    wanted = set().union(*((counterparts or {}).get(doc, set()) for doc in kept_docs))
    extra: list[tuple[Hit, float]] = []
    for pair in ranked[KEEP:]:
        doc = pair[0].doc_id
        if doc in wanted and doc not in kept_docs and doc not in {h.doc_id for h, _ in extra}:
            extra.append(pair)
    return Selection(kept=tuple(kept), counterparts=tuple(extra))


def _cross_encoder_class() -> Any:
    # The model is pinned on disk; never look for it online (the firewall blocks it anyway).
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    from sentence_transformers import CrossEncoder

    return CrossEncoder


class Reranker:
    """The product's reranker: only the pinned small model is ever loaded here.

    Create one and reuse it; loading takes about a second.
    """

    def __init__(self) -> None:
        self._model = _cross_encoder_class()(MODEL, revision=pinned_revision(MODEL))

    def scores(self, question: str, passages: Sequence[str]) -> list[float]:
        if not passages:
            return []
        values = self._model.predict([(question, text) for text in passages], batch_size=10)
        return [float(value) for value in values]

    def rerank(
        self,
        question: str,
        hits: Sequence[Hit],
        counterparts: Mapping[str, set[str]] | None = None,
        threshold: float | None = None,
    ) -> Selection:
        floor = thresholds()[language_of(question)] if threshold is None else threshold
        values = self.scores(question, [f"{h.label}\n{h.text}" for h in hits])
        return select(list(zip(hits, values, strict=True)), floor, counterparts)
