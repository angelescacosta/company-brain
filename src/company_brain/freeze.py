"""Freeze the configuration (T37).

Retrieval settings come from T35's stage winners; the answering model follows the
written policy: eligible models have zero hard blockers on the tuning set and meet
every T33 performance target; among them the higher mean owner score wins, ties go
to fewer guardrail warnings, then lower answer latency, then the model name. On the
single-model path the one eligible model is chosen if its mean owner score is at
least 3.5, otherwise the status is "pilot, below target".
"""

import csv
import hashlib
import json
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SELECTED = REPO / "config" / "selected.toml"
MIN_SCORE = 3.5


@dataclass(frozen=True)
class Candidate:
    model: str
    hard_blockers: int
    meets_targets: bool
    mean_score: float | None
    warnings: int
    latency_s: float


def choose(candidates: Sequence[Candidate], single_model: bool) -> tuple[str, str | None]:
    """(status, model) by the T37 policy."""
    eligible = [c for c in candidates if c.hard_blockers == 0 and c.meets_targets]
    if not eligible:
        return "no eligible model", None
    best = min(
        eligible,
        key=lambda c: (
            -(c.mean_score if c.mean_score is not None else float("-inf")),
            c.warnings,
            c.latency_s,
            c.model,
        ),
    )
    if single_model and (best.mean_score is None or best.mean_score < MIN_SCORE):
        return "pilot, below target", best.model
    return "selected", best.model


def mean_scores(ratings: Path) -> dict[str, float]:
    by_model: dict[str, list[int]] = {}
    with ratings.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            by_model.setdefault(row["model"], []).append(int(row["score"]))
    return {m: sum(v) / len(v) for m, v in by_model.items()}


def run_metrics(runs_csv: Path, run_id: str) -> dict[str, float]:
    """The 'all languages' metrics of one run in `reports/eval_runs.csv`."""
    with runs_csv.open(encoding="utf-8") as handle:
        return {
            r["metric"]: float(r["value"])
            for r in csv.DictReader(handle)
            if r["run_id"] == run_id and r["language"] == "all"
        }


def blocker_count(runs_csv: Path, run_id: str) -> int:
    with runs_csv.open(encoding="utf-8") as handle:
        rows = [
            r
            for r in csv.DictReader(handle)
            if r["run_id"] == run_id and r["metric"] == "hard_blockers" and r["language"] == "all"
        ]
    if not rows:
        raise ValueError(f"run {run_id} has no hard-blocker row")
    return int(rows[0]["count"])


def stage_winner(stage: dict[str, Any]) -> dict[str, Any]:
    """T35's written rule: highest recall@5, then MRR, then speed."""
    best = max(stage["results"], key=lambda r: (r["recall@5"], r["mrr"], -r["seconds"]))
    return dict(best["config"])


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def to_toml(tables: dict[str, dict[str, Any]], header: str) -> str:
    lines = [f"# {line}" for line in header.splitlines()]
    for name, table in tables.items():
        lines += ["", f"[{name}]"]
        lines += [f"{key} = {_toml_value(value)}" for key, value in table.items()]
    return "\n".join(lines) + "\n"


def load_selected(path: Path = SELECTED) -> dict[str, Any] | None:
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def frozen_retrieval(path: Path = SELECTED) -> tuple[str, int] | None:
    """(embedding model, chunk budget) of the frozen configuration, if there is one."""
    selected = load_selected(path)
    if not selected:
        return None
    return selected["retrieval"]["embedding"], int(selected["retrieval"]["budget"])


def now() -> str:
    return f"{datetime.now():%Y-%m-%d %H:%M}"
