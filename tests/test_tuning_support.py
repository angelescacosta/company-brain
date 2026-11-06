"""T35 support: candidate indexes are built without becoming the one the assistant uses, and
retrieval-only stages append their rows to the results file (tuning rows only)."""

import csv
from pathlib import Path

import pytest

from company_brain import eval_run, indexing, ingest


class Conn:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.parametrize(
    ("state", "wanted", "activated"),
    [
        ("completed", True, ["i1"]),
        ("completed", False, []),  # a tuning candidate: the assistant keeps its index
        ("stopped", True, []),
    ],
)
def test_only_a_complete_index_that_is_wanted_is_activated(
    monkeypatch: pytest.MonkeyPatch, state: str, wanted: bool, activated: list[str]
) -> None:
    done: list[str] = []
    monkeypatch.setattr(indexing, "activate_index", lambda _conn, index: done.append(index))
    indexing.finish(Conn(), "i1", ingest.RunResult("r", state=state), activate=wanted)  # type: ignore[arg-type]
    assert done == activated


def test_rows_are_appended_under_one_header(tmp_path: Path) -> None:
    path = tmp_path / "eval_runs.csv"
    row = {f: "x" for f in eval_run.CSV_FIELDS} | {"split": "tuning"}
    eval_run.append_rows([row], path)
    eval_run.append_rows([row, row], path)
    with path.open() as handle:
        assert len(list(csv.DictReader(handle))) == 3
    assert path.read_text().count("run_id") == 1
    with pytest.raises(PermissionError):
        eval_run.append_rows([{**row, "split": "locked"}], path)
