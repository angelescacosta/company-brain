"""T22: selection after reranking, conflict counterparts, and which model is loaded.

PBT on selection: for any finite score list, possibly empty, the result is
sorted, holds at most 5 kept chunks plus counterparts, and is "not found"
exactly when the list is empty or the best score is below the threshold;
non-finite scores are rejected (protects the no-source-no-answer rule).
"""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from company_brain import rerank
from company_brain.retrieval import Hit


def hit(n: int, doc: str = "d") -> Hit:
    return Hit(f"c{n:03d}", f"{doc}{n}", "A", "A", f"x/{doc}{n}", "text", "label", None, (), 0.0)


scores = st.lists(st.floats(-20, 20, allow_nan=False), max_size=40)
thresholds = st.floats(-20, 20, allow_nan=False)


@given(scores, thresholds)
def test_selection(values: list[float], threshold: float) -> None:
    hits = [hit(n) for n in range(len(values))]
    result = rerank.select(list(zip(hits, values, strict=True)), threshold)
    if not values or max(values) < threshold:
        assert result.not_found and result.kept == ()
        return
    assert not result.not_found
    kept = [score for _hit, score in result.kept]
    assert kept == sorted(kept, reverse=True)
    assert 1 <= len(kept) <= rerank.KEEP
    assert kept[0] == max(values)


@given(st.sampled_from([math.nan, math.inf, -math.inf]))
def test_non_finite_scores_are_rejected(bad: float) -> None:
    with pytest.raises(ValueError):
        rerank.select([(hit(0), 1.0), (hit(1), bad)], 0.0)


def test_counterpart_is_re_added_when_its_partner_is_kept() -> None:
    draft = Hit("c900", "draft", "A", "A", "x/draft.docx", "fee 52,000", "l", None, (), 0.0)
    signed = Hit("c901", "signed", "A", "A", "x/signed.docx", "fee 48,000", "l", None, (), 0.0)
    others = [(hit(n), 10.0 - n) for n in range(8)]
    scored = [(signed, 9.5), *others, (draft, -3.0)]
    result = rerank.select(scored, 0.0, counterparts={"signed": {"draft"}, "draft": {"signed"}})
    ids = [h.chunk_id for h, _s in result.kept]
    assert "c901" in ids and len(ids) == rerank.KEEP
    assert [h.chunk_id for h, _s in result.counterparts] == ["c900"]


def test_only_the_pinned_small_reranker_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    """T22.AC4: the product's reranker is mMiniLMv2 at the pinned revision; never bge."""
    loaded: list[tuple[str, str]] = []

    class Spy:
        def __init__(self, name: str, revision: str, **_kwargs: object) -> None:
            loaded.append((name, revision))

        def predict(self, pairs, **_kwargs):  # type: ignore[no-untyped-def]
            return [0.0 for _ in pairs]

    monkeypatch.setattr(rerank, "_cross_encoder_class", lambda: Spy)
    rerank.Reranker().scores("q", ["a", "b"])
    pinned = rerank.pinned_revision(rerank.MODEL)
    assert loaded == [("cross-encoder/mmarco-mMiniLMv2-L12-H384-v1", pinned)]
    assert "bge" not in rerank.MODEL


def test_language_thresholds_are_configured() -> None:
    assert set(rerank.thresholds()) == {"en", "es"}
    assert rerank.language_of("¿Cuál es el plazo de pago?") == "es"
    assert rerank.language_of("What is the payment term?") == "en"


@pytest.mark.integration
def test_counterparts_come_from_the_same_scope_only() -> None:
    from ingest_helpers import fresh_db

    from company_brain.retrieval import ScopeError, counterpart_map

    conn = fresh_db()
    for doc, scope, status, kind in [
        ("a-draft", "A", "draft", "draft"),
        ("a-signed", "A", "signed", "contract"),
        ("b-signed", "B", "signed", "contract"),
        ("a-report", "A", None, "report"),
    ]:
        conn.execute(
            """INSERT INTO brain.documents (doc_id, client, scope, doc_type, language, status,
                                            file_hash, availability, rel_path)
               VALUES (%s, %s, %s, %s, 'en', %s, 'h', 'available', %s)""",
            (doc, scope, scope, kind, status, f"x/{doc}"),
        )
    conn.commit()
    assert counterpart_map(conn, "A", "A") == {"a-draft": {"a-signed"}, "a-signed": {"a-draft"}}
    with pytest.raises(ScopeError):
        counterpart_map(conn, "A", "B")
    conn.close()
