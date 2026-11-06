"""Test questions: validation and the tuning/locked split (T14).

The full pack is written to `eval/review/all.json` (git-ignored). `split()`
locks one client per language and tunes on the others, so the locked set
shares no client and no document with tuning. `scripts/split_questions.py`
writes the tuning part to `eval/tuning.json` (committed) and the locked part to
`eval/review/locked.json` (never committed; sealed in T14S).
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA = Path(__file__).resolve().parents[2] / "eval" / "schema.json"
SEED = 20261024
CATEGORIES = ("fact", "table_chart", "multi_document", "summary", "unanswerable", "adversarial")
# Per language, across tuning and locked together.
TARGET_COUNTS = {
    "fact": 10,
    "table_chart": 5,
    "multi_document": 5,
    "summary": 5,
    "unanswerable": 5,
    "adversarial": 3,
}
# The fixed message Answer mode returns when the relevance floor triggers (T24).
NOT_FOUND = {
    "en": "I could not find this in the documents.",
    "es": "No he encontrado esta información en los documentos.",
}


class Infeasible(ValueError):
    """The requested split cannot keep every category in both parts."""


def validate(pack: dict[str, Any]) -> None:
    jsonschema.validate(pack, json.loads(SCHEMA.read_text(encoding="utf-8")))


def split(
    items: Sequence[dict[str, Any]], seed: int = SEED
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Lock one client per language, chosen by the seed among the feasible ones."""
    locked_clients = set()
    for language in sorted({q["language"] for q in items}):
        own = [q for q in items if q["language"] == language]
        clients = sorted({q["client"] for q in own})
        if len(clients) < 3:
            raise Infeasible(f"{language}: {len(clients)} clients, at least 3 needed")

        def categories(group: set[str], questions: list[dict[str, Any]] = own) -> set[str]:
            return {q["category"] for q in questions if q["client"] in group}

        feasible = [
            client
            for client in clients
            if categories({client}) == set(CATEGORIES)
            and categories(set(clients) - {client}) == set(CATEGORIES)
        ]
        if not feasible:
            raise Infeasible(
                f"{language}: no client can be locked with every category on both sides"
            )
        locked_clients.add(random.Random(f"{seed}:{language}").choice(feasible))  # noqa: S311
    tuning = [q for q in items if q["client"] not in locked_clients]
    locked = [q for q in items if q["client"] in locked_clients]
    return tuning, locked
