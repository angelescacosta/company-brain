"""T35: the tuning record follows the plan. Run after `scripts/run_tuning.py` (stages 1 to 3 and
the floors) and the stage 4 evaluation; it re-derives every decision from the saved results."""

import csv
import json
import tomllib
from datetime import date
from pathlib import Path

import pytest

from company_brain import tuning
from company_brain.tuning import StageResult

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "reports" / "tuning"

pytestmark = pytest.mark.skipif(
    not (OUT / "stage3.json").exists(), reason="the tuning stages have not run yet"
)


def stage(name: str) -> dict:  # type: ignore[type-arg]
    return json.loads((OUT / f"{name}.json").read_text())


def csv_rows(run_id: str) -> list[dict[str, str]]:
    with (REPO / "reports" / "eval_runs.csv").open() as handle:
        return [r for r in csv.DictReader(handle) if r["run_id"] == run_id]


@pytest.mark.parametrize("name", ["stage1", "stage2", "stage3"])
def test_every_stage_has_rows_and_its_winner_follows_the_rule(name: str) -> None:
    """T35.AC1: the winner is recomputed from the CSV rows, not taken from the record."""
    record = stage(name)
    rows = csv_rows(record["run_id"])
    assert (
        rows and {r["split"] for r in rows} == {"tuning"} and {r["stage"] for r in rows} == {name}
    )
    results = []
    for entry in record["results"]:
        mine = {
            r["metric"]: float(r["value"])
            for r in rows
            if json.loads(r["config"]) == entry["config"] and r["language"] == "all"
        }
        results.append(StageResult(entry["config"], mine["recall@5"], mine["mrr"], mine["seconds"]))
    assert tuning.winner(results).config == record["winner"]


def test_each_stage_builds_on_the_previous_winner() -> None:
    first, second, third = stage("stage1"), stage("stage2"), stage("stage3")
    assert {r["config"]["embedding"] for r in second["results"]} == {first["winner"]["embedding"]}
    assert [r["config"]["budget"] for r in second["results"]] == [300, 500, 800]
    assert {(r["config"]["embedding"], r["config"]["budget"]) for r in third["results"]} == {
        (second["winner"]["embedding"], second["winner"]["budget"])
    }
    assert [r["config"]["reranker"] for r in third["results"]] == ["on", "off"]


def test_the_sealing_key_was_not_read_during_tuning() -> None:
    """T35.AC2: the seal access log did not grow during any stage (cooperative guard, T14S)."""
    for name in ("stage1", "stage2", "stage3"):
        before, after = stage(name)["seal_log_lines"]
        assert before == after, name


def test_stage1_estimate_follows_the_eight_hour_rule() -> None:
    """T35.AC4."""
    estimate = stage("stage1_estimate")
    assert estimate["keep_embeddinggemma"] == (estimate["hours"] <= 8.0)
    models = {r["config"]["embedding"] for r in stage("stage1")["results"]}
    assert ("embeddinggemma-2:270m" in models) == estimate["keep_embeddinggemma"]


def test_stage3_records_reranker_on_and_off_per_language() -> None:
    """T35.AC5: recall@5 and MRR per language with counts, and the on-minus-off difference;
    a drop in either measure in either language has a decision in docs/DECISIONS.md."""
    on, off = stage("stage3")["results"]
    dropped = False
    for language in ("en", "es"):
        assert on["counts"][language] == off["counts"][language] > 0
        for metric in ("recall@5", "mrr"):
            difference = on["by_language"][language][metric] - off["by_language"][language][metric]
            dropped = dropped or difference < 0
    if dropped:
        assert "reranker" in (REPO / "docs" / "DECISIONS.md").read_text().split("(T35")[-1]


def test_floors_were_chosen_by_the_rule_and_written() -> None:
    floors = stage("floors")
    candidates = [x / 2 for x in range(-20, 21)]
    written = tomllib.loads((REPO / "config" / "relevance.toml").read_text())["threshold"]
    for language, scored in floors["scored"].items():
        pairs = [(float(s), bool(a)) for s, a in scored]
        assert tuning.threshold_choice(pairs, candidates) == floors["chosen"][language]
        assert written[language] == floors["chosen"][language]


def test_finished_by_31_october() -> None:
    """T35.AC3."""
    for name in ("stage1", "stage2", "stage3"):
        day = stage(name)["run_id"][:8]
        assert date(int(day[:4]), int(day[4:6]), int(day[6:])) <= date(2026, 10, 31)
