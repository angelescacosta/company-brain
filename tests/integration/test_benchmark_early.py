"""T7: the early benchmark has run on this Mac and its results follow the rule.

Marker `slow`: running the benchmark takes about 15 minutes, so this test checks
the results written by `scripts/benchmark_early.py` and re-applies the roster
rule to them. Run the script again to refresh them.
"""

import csv
import tomllib
from pathlib import Path

import pytest

from company_brain.benchmark import ANSWER_MODEL, FIELDS, PRESSURE_NAMES, BenchmarkRow, roster

pytestmark = [pytest.mark.integration, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]


def test_results_and_roster() -> None:
    with (REPO / "reports" / "benchmark_early.csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows and list(rows[0]) == FIELDS
    assert [r["model"] for r in rows] == [ANSWER_MODEL]
    levels = {name: level for level, name in PRESSURE_NAMES.items()}
    table = []
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
        assert row.pressure_samples > 0
        assert row.first_token_s > 0 and row.total_s >= row.first_token_s
        assert row.peak_memory >= row.baseline_memory > 0
        table.append(row)
    written = tomllib.loads((REPO / "config" / "roster.toml").read_text())["roster"]
    assert written["models"] == roster(table)
