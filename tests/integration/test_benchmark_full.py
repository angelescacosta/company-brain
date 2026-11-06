"""T33: the full-pipeline benchmark has run on this Mac and its results follow the rule.

Marker `slow`: the benchmark takes about an hour, so this test checks the results
written by `scripts/benchmark_full.py` and re-applies the targets to them. Run the
script again to refresh them.
"""

import csv
import tomllib
from pathlib import Path

import pytest

from company_brain.benchmark import (
    ANSWER_MODEL,
    FULL_FIELDS,
    PRESSURE_NAMES,
    TARGETS_S,
    WARM_RUNS,
    BenchmarkRow,
    FullRun,
    full_verdict,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]


def test_results_and_roster() -> None:
    with (REPO / "reports" / "benchmark_full.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows and list(rows[0]) == FULL_FIELDS
    levels = {name: level for level, name in PRESSURE_NAMES.items()}
    runs = []
    for r in rows:
        row = BenchmarkRow(
            model=r["model"],
            baseline_memory=int(r["baseline_memory_bytes"]),
            peak_memory=int(r["peak_memory_bytes"]),
            swap_growth=int(r["swap_growth_bytes"]),
            first_token_s=float(r["first_token_s"]),
            total_s=float(r["total_s"]),
            peak_pressure=levels.get(r["peak_pressure"], 0),
            pressure_samples=int(r["pressure_samples"]),
            longest_gap_s=float(r["longest_gap_s"]),
            pressure_verdict=r["pressure_verdict"],
        )
        assert row.model == ANSWER_MODEL
        assert row.pressure_samples > 0 and row.total_s > 0
        assert row.peak_memory >= row.baseline_memory > 0
        runs.append(FullRun(row, r["mode"], int(r["run"]), r["cold"] == "yes"))
    for mode in TARGETS_S:  # every enabled mode: one cold run (T33.AC2), three warm runs
        mine = [run for run in runs if run.mode == mode]
        assert sum(run.cold for run in mine) == 1
        assert sum(not run.cold for run in mine) == WARM_RUNS
    verdict = full_verdict(runs)
    written = tomllib.loads((REPO / "config" / "roster.toml").read_text())
    assert written["full_benchmark"]["result"] == verdict["result"]  # T33.AC3
    missed = [mode for mode, m in verdict["modes"].items() if not m["met"]]
    assert written["full_benchmark"]["missed"] == missed
    assert verdict["result"] == "pass", verdict  # T33.AC1
