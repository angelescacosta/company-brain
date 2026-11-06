"""T24.AC4: the preliminary runtime estimate exists and says whether T35 fits."""

from pathlib import Path

from company_brain.runtime_estimate import Timings, project

REPO = Path(__file__).resolve().parents[1]


def test_projection_and_decision() -> None:
    fast = Timings(
        answer_s=30,
        judge_s=10,
        retrieve_s=0.5,
        rerank_s=0.6,
        convert_s=600,
        embed_s_per_chunk=0.03,
        chunks=1500,
    )
    plan = project(fast)
    assert plan.fits and plan.reduced is False
    slow = Timings(
        answer_s=400,
        judge_s=200,
        retrieve_s=1,
        rerank_s=1,
        convert_s=20000,
        embed_s_per_chunk=0.5,
        chunks=20000,
    )
    plan = project(slow)
    assert not plan.fits and plan.reduced is True
    assert plan.total_hours == sum(plan.stages.values())


def test_report_exists_and_states_the_decision() -> None:
    text = (REPO / "reports" / "runtime_estimate.md").read_text(encoding="utf-8")
    assert "Fits by 31 October:" in text
    assert "Answer (median)" in text and "Total" in text
    assert ("Fits by 31 October: yes" in text) or ("reduced plan" in text.lower())
