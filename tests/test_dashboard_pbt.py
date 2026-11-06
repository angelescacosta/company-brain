"""T34D PBT: for any generated set of rows within the input schema, including empty and
partial sets and real zeros, every aggregate shown equals the recomputed aggregate,
missing data is "pending" and zero is a value, and building twice from the same files
gives identical bytes (protects against a chart that misreports or changes between builds)."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from company_brain import dashboard

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_dashboard import Cells, eval_rows, write_csv  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
CONFIGS = [
    {"answer_model": "qwen3.5:4b", "budget": b, "embedding": "bge-m3:latest", "reranker": r}
    for b in (300, 500)
    for r in ("on", "off")
]

row = st.tuples(
    st.integers(0, 3),  # run number (later runs win)
    st.sampled_from(range(len(CONFIGS))),
    st.sampled_from(["recall@5", "mrr"]),
    st.sampled_from(["en", "es", "all"]),
    st.integers(1, 50),  # total
    st.integers(0, 50),  # count before capping
)


def expected(rows: list[tuple[int, int, str, str, int, int]]) -> dict[str, tuple[float, int]]:
    """Independent recomputation: the latest run's value per setting, metric and language."""
    best: dict[str, tuple[int, float, int]] = {}
    for run, config, metric, language, total, count in rows:
        key = f"search|{dashboard.config_label(CONFIGS[config])}|{metric}|{language}"
        value = min(count, total) / total
        if key not in best or run >= best[key][0]:
            best[key] = (run, value, total)
    return {k: (v, n) for k, (_r, v, n) in best.items()}


def shown(metric: str, value: float, n: int) -> str:
    if metric == "mrr":
        return f"{value:.2f}, n = {n}"
    return f"{100 * value:.1f}%, n = {n}"


@settings(max_examples=60, deadline=None)
@given(st.lists(row, max_size=25))
def test_aggregates_pending_zero_and_identical_builds(rows: list) -> None:  # type: ignore[type-arg]
    # One run per (run number); a run writes each (setting, metric, language) once.
    unique = {(r[0], r[1], r[2], r[3]): r for r in rows}
    rows = list(unique.values())
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder) / "repo"
        (root / "config").mkdir(parents=True)
        shutil.copyfile(
            REPO / "config" / "dashboard_inputs.toml", root / "config" / "dashboard_inputs.toml"
        )
        (root / "data" / "manifests").mkdir(parents=True)
        for name in ("fictional.json", "public.json"):
            (root / "data" / "manifests" / name).write_text(json.dumps({"files": []}))
        csv_rows = []
        for run, config, metric, language, total, count in rows:
            csv_rows += eval_rows(
                f"run{run}-{config}",
                f"2026-10-2{run} 10:00",
                CONFIGS[config],
                {(metric, language): (min(count, total) / total, min(count, total), total)},
            )
        if csv_rows:
            write_csv(root / "reports" / "eval_runs.csv", csv_rows)
        paths = dashboard.Paths(root, Path(folder) / "docs", Path(folder) / "copies")
        assert dashboard.build(paths)["result"] == "succeeded"
        first = (root / "reports" / "dashboard.html").read_bytes()
        assert dashboard.build(paths)["result"] == "succeeded"
        assert (root / "reports" / "dashboard.html").read_bytes() == first
    cells = Cells()
    cells.feed(first.decode())
    want = expected(rows)
    if not want:
        assert cells.cells["search"] == "pending"
        return
    labels = {dashboard.config_label(CONFIGS[r[1]]) for r in rows}
    for label in labels:
        for metric in ("recall@5", "mrr"):
            for language in ("en", "es", "all"):
                key = f"search|{label}|{metric}|{language}"
                if key in want:
                    value, n = want[key]
                    assert cells.cells[key] == shown(metric, value, n), key
                    assert cells.bars[key] == round(value * dashboard.BAR, 1)
                    green = value >= dashboard.TARGETS[metric]
                    assert cells.classes[key] == ("green" if green else "red")
                else:
                    assert cells.cells[key] == "pending", key
