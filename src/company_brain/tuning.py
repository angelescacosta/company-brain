"""Staged tuning on the tuning questions (T35).

Stages 1 to 3 are retrieval only: each configuration is scored by recall@5 and MRR
on the answerable questions (as T34 defines them), with the passages in the order
the product would use them (the reranker's order when it is on), and timed. The
winner of a stage follows the written rule: highest recall@5, then MRR, then speed,
ties to the earlier configuration. Stage 4 sets each language's relevance floor from
the reranker's best score per question, then runs the full evaluation once.
"""

from __future__ import annotations

import statistics
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from company_brain import evaluation as ev

MAX_STAGE1_HOURS = 8.0  # T35.AC4: over this, EmbeddingGemma 2 is dropped from stage 1
LANGUAGES = ("en", "es", "all")


@dataclass(frozen=True)
class StageResult:
    config: dict[str, Any]
    recall: float  # recall@5 over both languages
    mrr: float
    seconds: float  # mean seconds per question for search (and reranking)
    by_language: dict[str, dict[str, float]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)


def winner(results: Sequence[StageResult]) -> StageResult:
    """Highest recall@5, then MRR, then speed; ties go to the earlier configuration."""
    if not results:
        raise ValueError("a stage needs at least one configuration")
    best = results[0]
    for result in results[1:]:
        if (result.recall, result.mrr, -result.seconds) > (best.recall, best.mrr, -best.seconds):
            best = result
    return best


def stage1_estimate(
    chunks: int, seconds_per_20: dict[str, float], conversion_minutes: float, models: Sequence[str]
) -> dict[str, Any]:
    """Hours to index every stage 1 model that has no index yet, and the T35.AC4 decision."""
    hours = sum(conversion_minutes / 60 + chunks / 20 * seconds_per_20[m] / 3600 for m in models)
    return {
        "models": list(models),
        "hours": round(hours, 2),
        "limit_hours": MAX_STAGE1_HOURS,
        "keep_embeddinggemma": hours <= MAX_STAGE1_HOURS,
    }


def threshold_choice(scored: Sequence[tuple[float, bool]], candidates: Sequence[float]) -> float:
    """The floor that sends the most questions the right way: an answerable question's best
    score at or above it, an unanswerable one's below. Ties go to the lower floor (fewer
    answerable questions refused), then to the earlier candidate."""
    if not candidates:
        raise ValueError("no candidate floors")

    def right(floor: float) -> tuple[int, int]:
        answered = sum(1 for score, answerable in scored if answerable and score >= floor)
        refused = sum(1 for score, answerable in scored if not answerable and score < floor)
        return answered + refused, answered

    best = candidates[0]
    for floor in candidates[1:]:
        if right(floor) > right(best) or (right(floor) == right(best) and floor < best):
            best = floor
    return best


@dataclass(frozen=True)
class Searched:
    """One answerable question: its required spans and the passages in the product's order."""

    id: str
    language: str
    spans: list[str]
    ranked: list[str]
    seconds: float


def search_all(
    questions: Sequence[dict[str, Any]],
    spans_of: Callable[[dict[str, Any]], list[str]],
    search: Callable[[str, str], list[str]],
) -> list[Searched]:
    """Time `search(question, scope)` on each answerable question (T34's retrieval set)."""
    found = []
    for q in questions:
        if q["mode"] == "summarise" or q["category"] == "unanswerable":
            continue
        began = time.monotonic()
        ranked = search(q["question"], f"fictional:{q['client']}")
        found.append(
            Searched(q["id"], q["language"], spans_of(q), ranked, time.monotonic() - began)
        )
    return found


def score(config: dict[str, Any], searched: Sequence[Searched]) -> StageResult:
    by_language: dict[str, dict[str, float]] = {}
    counts: dict[str, int] = {}
    for language in LANGUAGES:
        mine = [s for s in searched if language == "all" or s.language == language]
        counts[language] = len(mine)
        if mine:
            spans, ranked = [s.spans for s in mine], [s.ranked for s in mine]
            by_language[language] = {
                "recall@5": ev.recall_at_k(spans, ranked, 5),
                "mrr": ev.mrr(spans, ranked),
                "seconds": statistics.mean(s.seconds for s in mine),
            }
    overall = by_language.get("all", {"recall@5": 0.0, "mrr": 0.0, "seconds": 0.0})
    return StageResult(
        config,
        overall["recall@5"],
        overall["mrr"],
        round(overall["seconds"], 3),
        by_language,
        counts,
    )


def rows(run_id: str, stage: str, result: StageResult) -> list[dict[str, Any]]:
    """`reports/eval_runs.csv` rows (T34's columns) for one retrieval-only configuration."""
    import json

    base = {
        "run_id": run_id,
        "date": f"{datetime.now():%Y-%m-%d %H:%M}",
        "split": "tuning",
        "stage": stage,
        "config": json.dumps(result.config, sort_keys=True),
    }
    out = []
    for language, metrics in result.by_language.items():
        total = result.counts[language]
        for metric, value in metrics.items():
            count = round(value * total) if metric == "recall@5" else total
            out.append(
                {
                    **base,
                    "metric": metric,
                    "language": language,
                    "value": round(value, 4),
                    "count": count,
                    "total": total,
                }
            )
    return out
