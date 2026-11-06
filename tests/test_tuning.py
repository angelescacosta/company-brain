"""T35 unit tests: the stage winner rule (PBT), the stage 1 estimate, the relevance floor and the
retrieval-only scoring and rows."""

from hypothesis import given
from hypothesis import strategies as st

from company_brain import tuning
from company_brain.tuning import Searched, StageResult

values = st.floats(min_value=0, max_value=1, allow_nan=False)
seconds = st.floats(min_value=0.01, max_value=10, allow_nan=False)


@given(st.lists(st.tuples(values, values, seconds), min_size=1, max_size=8))
def test_the_winner_is_maximal_under_the_rule(table: list[tuple[float, float, float]]) -> None:
    """Protects the choice of every stage: for any results table, nothing beats the winner on
    recall@5, then MRR, then speed, and a tie goes to the earliest configuration."""
    results = [StageResult({"n": n}, r, m, s) for n, (r, m, s) in enumerate(table)]
    best = tuning.winner(results)
    key = (best.recall, best.mrr, -best.seconds)
    assert all((r.recall, r.mrr, -r.seconds) <= key for r in results)
    first = next(r for r in results if (r.recall, r.mrr, -r.seconds) == key)
    assert best is first


def test_recall_first_then_mrr_then_speed() -> None:
    a = StageResult({"n": "a"}, 0.9, 0.5, 1.0)
    b = StageResult({"n": "b"}, 0.8, 0.9, 0.1)
    c = StageResult({"n": "c"}, 0.9, 0.6, 2.0)
    d = StageResult({"n": "d"}, 0.9, 0.6, 1.5)
    assert tuning.winner([a, b]) is a
    assert tuning.winner([a, c]) is c
    assert tuning.winner([c, d]) is d


def test_stage1_estimate_and_the_eight_hour_rule() -> None:
    """T35.AC4."""
    speeds = {"qwen3-embedding:0.6b": 0.488, "embeddinggemma-2:270m": 0.383}
    small = tuning.stage1_estimate(1856, speeds, 10.0, list(speeds))
    assert small["hours"] == round(2 * 10 / 60 + 1856 / 20 * (0.488 + 0.383) / 3600, 2)
    assert small["keep_embeddinggemma"] is True
    big = tuning.stage1_estimate(1856, speeds, 250.0, list(speeds))
    assert big["hours"] > 8 and big["keep_embeddinggemma"] is False


def test_the_floor_sends_most_questions_the_right_way() -> None:
    scored = [(5.0, True), (2.0, True), (-1.0, True), (-3.0, False), (0.5, False)]
    # -2: 3 answered + 1 refused = 4; 1: 2 answered + 2 refused = 4; tie goes to the lower
    assert tuning.threshold_choice(scored, [1.0, -2.0, 6.0]) == -2.0
    assert tuning.threshold_choice([(3.0, False), (4.0, True)], [3.5, -8.0]) == 3.5
    # an exact tie on both counts: the lower floor
    assert tuning.threshold_choice([(5.0, True), (-3.0, False)], [0.0, -1.0]) == -1.0


def test_search_all_skips_summaries_and_unanswerable_and_scores_by_language() -> None:
    questions = [
        {
            "id": "q1",
            "question": "fee?",
            "client": "A",
            "language": "en",
            "mode": "answer",
            "category": "fact",
        },
        {
            "id": "q2",
            "question": "¿tarifa?",
            "client": "B",
            "language": "es",
            "mode": "answer",
            "category": "fact",
        },
        {
            "id": "q3",
            "question": "who?",
            "client": "A",
            "language": "en",
            "mode": "answer",
            "category": "unanswerable",
        },
        {
            "id": "q4",
            "question": "sum",
            "client": "A",
            "language": "en",
            "mode": "summarise",
            "category": "summary",
        },
    ]
    seen: list[tuple[str, str]] = []

    def search(question: str, scope: str) -> list[str]:
        seen.append((question, scope))
        return ["noise", "the fee is EUR 10"] if scope.endswith("A") else ["nothing"]

    found = tuning.search_all(questions, lambda q: ["the fee is EUR 10"], search)
    assert [s.id for s in found] == ["q1", "q2"] and seen[0] == ("fee?", "fictional:A")
    result = tuning.score({"model": "m"}, found)
    assert result.by_language["en"]["recall@5"] == 1.0 and result.by_language["en"]["mrr"] == 0.5
    assert result.by_language["es"]["recall@5"] == 0.0
    assert (result.recall, result.mrr) == (0.5, 0.25)
    rows = tuning.rows("r1", "stage1", result)
    recall = {
        (r["language"]): (r["value"], r["count"], r["total"])
        for r in rows
        if r["metric"] == "recall@5"
    }
    assert recall == {"en": (1.0, 1, 1), "es": (0.0, 0, 1), "all": (0.5, 1, 2)}
    assert {r["split"] for r in rows} == {"tuning"} and {r["stage"] for r in rows} == {"stage1"}


def test_scoring_uses_the_order_it_is_given() -> None:
    """Reranker on and off differ only in passage order, so the order must be what is scored."""
    spans = ["the fee is EUR 10"]
    first = Searched("q", "en", spans, ["the fee is EUR 10", "x"], 0.1)
    second = Searched("q", "en", spans, ["x", "the fee is EUR 10"], 0.1)
    assert tuning.score({}, [first]).mrr == 1.0 and tuning.score({}, [second]).mrr == 0.5
