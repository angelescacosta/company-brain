"""T25: the claim score (HHEM-2.1-Open, pinned, offline) warns on an unsupported fact."""

import pytest

from company_brain import guardrails
from company_brain.answer import Answer, Fact, Source
from company_brain.retrieval import Hit

pytestmark = pytest.mark.integration


def test_hhem_scores_supported_above_unsupported() -> None:
    score = guardrails.hhem_scorer()
    hit = Hit(
        "c1",
        "d1",
        "A",
        "A",
        "x/1",
        "The pilot starts at the Leeds depot only.",
        "label",
        None,
        (),
        0.0,
    )
    good = Answer(
        "Where?",
        "en",
        "",
        facts=[Fact("The pilot starts in Leeds.", (1,))],
        sources=[Source(1, hit, 1.0)],
    )
    bad = Answer(
        "Where?",
        "en",
        "",
        facts=[Fact("The pilot starts in Hull and York.", (1,))],
        sources=[Source(1, hit, 1.0)],
    )
    assert not guardrails.gate(good, claim_score=score).warnings
    assert any(
        "not clearly supported" in w for w in guardrails.gate(bad, claim_score=score).warnings
    )
