"""T37: the freeze policy, the stage-winner rule and the frozen file."""

import json
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from company_brain import freeze
from company_brain.freeze import Candidate

REPO = Path(__file__).resolve().parents[1]


def c(model: str, blockers: int = 0, ok: bool = True, score: float | None = 4.0, warnings: int = 0,
      latency: float = 8.0) -> Candidate:  # fmt: skip
    return Candidate(model, blockers, ok, score, warnings, latency)


def test_single_model_selected_at_or_above_3_5() -> None:
    assert freeze.choose([c("a", score=3.5)], single_model=True) == ("selected", "a")
    assert freeze.choose([c("a", score=3.4)], single_model=True) == ("pilot, below target", "a")


def test_blockers_or_missed_targets_make_a_model_ineligible() -> None:
    assert freeze.choose([c("a", blockers=1)], True) == ("no eligible model", None)
    assert freeze.choose([c("a", ok=False)], True) == ("no eligible model", None)
    assert freeze.choose([c("a", blockers=1), c("b")], False) == ("selected", "b")


def test_ties_go_to_warnings_then_latency_then_name() -> None:
    assert freeze.choose([c("a", warnings=2), c("b", warnings=1)], False)[1] == "b"
    assert freeze.choose([c("a", latency=9), c("b", latency=8)], False)[1] == "b"
    assert freeze.choose([c("b"), c("a")], False)[1] == "a"


@given(
    st.lists(
        st.builds(
            Candidate,
            st.sampled_from(["m1", "m2", "m3", "m4"]),
            st.integers(0, 2),
            st.booleans(),
            st.one_of(st.none(), st.floats(1, 5)),
            st.integers(0, 3),
            st.floats(1, 60),
        ),
        max_size=4,
        unique_by=lambda x: x.model,
    ),
    st.booleans(),
)
def test_policy_follows_the_rule(candidates: list[Candidate], single: bool) -> None:
    status, model = freeze.choose(candidates, single)
    eligible = [x for x in candidates if x.hard_blockers == 0 and x.meets_targets]
    if not eligible:
        assert (status, model) == ("no eligible model", None)
        return
    chosen = next(x for x in eligible if x.model == model)
    for other in eligible:
        mine = chosen.mean_score if chosen.mean_score is not None else float("-inf")
        theirs = other.mean_score if other.mean_score is not None else float("-inf")
        assert mine >= theirs
    if single and (chosen.mean_score is None or chosen.mean_score < freeze.MIN_SCORE):
        assert status == "pilot, below target"
    else:
        assert status == "selected"


def test_stage_winner_rule() -> None:
    stage = {
        "results": [
            {"config": {"x": 1}, "recall@5": 1.0, "mrr": 0.9, "seconds": 0.3},
            {"config": {"x": 2}, "recall@5": 1.0, "mrr": 0.9, "seconds": 0.2},
            {"config": {"x": 3}, "recall@5": 0.9, "mrr": 1.0, "seconds": 0.1},
        ]
    }
    assert freeze.stage_winner(stage) == {"x": 2}


def test_frozen_file_follows_the_policy_recomputed() -> None:
    """T37.AC1 on the real files (skipped before the freeze)."""
    selected = freeze.load_selected()
    if selected is None:
        return
    scores = freeze.mean_scores(REPO / "reports" / "blind_ratings_unblinded.csv")
    model = selected["answer"]["model"]
    assert abs(selected["answer"]["mean_owner_score"] - round(scores[model], 2)) < 1e-9
    run = selected["evidence"]["tuning_run"]
    blockers = freeze.blocker_count(REPO / "reports" / "eval_runs.csv", run)
    candidate = Candidate(model, blockers, selected["answer"]["meets_targets"],
                          scores[model], 0, selected["answer"]["latency_s"])  # fmt: skip
    assert freeze.choose([candidate], single_model=True) == (selected["answer"]["status"], model)
    stages = [json.loads((REPO / f"reports/tuning/stage{n}.json").read_text()) for n in (1, 2)]
    assert selected["retrieval"]["embedding"] == freeze.stage_winner(stages[0])["embedding"]
    assert selected["retrieval"]["budget"] == freeze.stage_winner(stages[1])["budget"]
    assert selected["retrieval"]["reranker"] == "on"  # D42 keeps it on against stage 3's rule


def test_toml_round_trip(tmp_path: Path) -> None:
    text = freeze.to_toml({"retrieval": {"embedding": "e", "budget": 500, "on": True}}, "head")
    path = tmp_path / "s.toml"
    path.write_text(text)
    assert freeze.frozen_retrieval(path) == ("e", 500)
