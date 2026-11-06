"""T38: the locked final evaluation report.

The unit tests check the report builder; the `release` test validates the real report
(AC1: exactly one locked run, every metric per language with counts and both hashes;
AC2: the hard-blocker count is stated)."""

import hashlib
from pathlib import Path

import pytest

from company_brain import final_eval

REPO = Path(__file__).resolve().parents[1]
BASE = {"run_id": "r", "date": "d", "split": "locked", "stage": "final", "config": "{}"}


def rows(value: float = 0.95) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for metric in final_eval.TARGETS:
        for language in final_eval.LANGUAGES:
            out.append(
                {
                    **BASE,
                    "metric": metric,
                    "language": language,
                    "value": value,
                    "count": 1,
                    "total": 1,
                }
            )
    return out


def test_status_rules() -> None:
    good = [{**r, "value": 1.0} for r in rows()]
    assert final_eval.status(good, 0) == ("meets every target", [])
    assert final_eval.status(rows(0.5), 0)[0] == "pilot, below target"
    assert final_eval.status(good, 1)[0] == "blocked"


def test_report_round_trip() -> None:
    text = final_eval.report(rows(), 0, "a" * 64, "b" * 64, "r", "d")
    facts = final_eval.parse(text)
    assert facts["hard_blockers"] == 0
    assert facts["config_sha"] == "a" * 64 and facts["locked_sha"] == "b" * 64
    assert {(m, lang) for m, lang, _v, _c in facts["table"]} == {
        (m, lang) for m in final_eval.TARGETS for lang in final_eval.LANGUAGES
    }


def test_judge_and_citation_rows() -> None:
    scored = [
        ("en", {"faithfulness": 1.0, "answer_relevancy": 0.5}),
        ("es", {"faithfulness": 0.5, "answer_relevancy": float("nan")}),
    ]
    out = final_eval.judge_rows(scored, BASE, {"en": 1, "es": 1, "all": 2})
    by = {(r["metric"], r["language"]): r["value"] for r in out}
    assert by[("faithfulness", "all")] == 0.75 and ("answer_relevancy", "es") not in by
    cites = final_eval.citation_rows({"en": 1}, {"en": 2, "es": 2, "all": 4}, BASE)
    assert {r["language"]: r["value"] for r in cites} == {"en": 0.5, "es": 1.0, "all": 0.75}


@pytest.mark.release
def test_the_real_report() -> None:
    assert len(final_eval.locked_runs(REPO / "reports" / "eval_runs.csv")) == 1
    facts = final_eval.parse(final_eval.REPORT.read_text(encoding="utf-8"))
    assert isinstance(facts["hard_blockers"], int)
    assert facts["locked_sha"] == hashlib.sha256(final_eval.LOCKED.read_bytes()).hexdigest()
    assert (
        facts["config_sha"]
        == hashlib.sha256((REPO / "config" / "selected.toml").read_bytes()).hexdigest()
    )
    present = {(m, lang) for m, lang, _v, _c in facts["table"]}
    for metric in final_eval.TARGETS:
        for language in final_eval.LANGUAGES:
            assert (metric, language) in present


def test_locked_rows_are_written_only_by_the_final_run(tmp_path: Path) -> None:
    from company_brain import eval_run

    locked = [{**BASE, "metric": "mrr", "language": "all", "value": 1.0, "count": 1, "total": 1}]
    with pytest.raises(PermissionError):
        eval_run.append_rows(locked, tmp_path / "runs.csv")
    eval_run.append_rows(locked, tmp_path / "runs.csv", locked_run=True)
    assert final_eval.locked_runs(tmp_path / "runs.csv") == {"r"}


def test_report_from_rows_shaped_like_the_script() -> None:
    """The same steps the script takes after judging, on made-up aggregates."""
    base = {**BASE}
    rows = [{**r} for r in rows_from_run()]
    rows += final_eval.judge_rows([("en", {"faithfulness": 0.9, "answer_relevancy": 0.8})], base,
                                  {"en": 1, "es": 1, "all": 2})  # fmt: skip
    rows += final_eval.citation_rows({}, {"en": 1, "es": 1, "all": 2}, base)
    text = final_eval.report(rows, 0, "a" * 64, "b" * 64, "r", "d")
    assert "Hard blockers: 0" in text and "citation_validity | all" in text


def rows_from_run() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for metric in ("recall@5", "mrr", "refusal", "questions_passed"):
        for language in final_eval.LANGUAGES:
            out.append({**BASE, "metric": metric, "language": language, "value": 1.0,
                        "count": 1, "total": 1})  # fmt: skip
    return out
