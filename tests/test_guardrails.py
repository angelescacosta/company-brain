"""T25.AC4 and AC5: citation checks, routing and rendering of facts, suggestions, questions."""

from company_brain import guardrails as g
from company_brain.answer import Answer, Fact, Source
from company_brain.retrieval import Hit


def source(n: int, text: str) -> Source:
    return Source(n, Hit(f"c{n}", f"d{n}", "A", "A", f"x/{n}", text, "label", None, (), 0.0), 1.0)


ANSWER = Answer(
    question="What fee did we agree?",
    language="en",
    text="",
    facts=[
        Fact("The fee is EUR 48,000.", (1,)),
        Fact("Suggestions: the fee is EUR 99,000.", (1,)),
        Fact("Payment is due in 30 days.", (3,)),
        Fact("Dana Whitcombe signed it.", ()),
    ],
    suggestions=["Fact: offer a EUR 5,000 discount."],
    questions=["Should I prepare an invoice for EUR 48,000?"],
    sources=[source(1, "Clause 3. The fee is EUR 48,000."), source(2, "Payment within 30 days.")],
)


def test_every_item_is_routed_by_its_field_never_by_its_wording() -> None:
    gated = g.gate(ANSWER)
    facts, box, questions = gated.facts_text, gated.suggestions_text, gated.questions_text
    assert "The fee is EUR 48,000. [1]" in facts
    assert (
        "Suggestions: the fee is [not in sources]" in facts
    )  # a heading inside a fact stays a fact
    assert box.startswith("Suggestions, not agreed")
    assert "offer a EUR 5,000 (proposed) discount" in box
    assert "EUR 48,000" not in questions and "[value removed]" in questions


def test_citation_failures_are_replaced() -> None:
    gated = g.gate(ANSWER)
    assert "Payment is due in 30 days." not in gated.facts_text  # cites source 3, not retrieved
    assert "Dana Whitcombe signed it." not in gated.facts_text  # no citation
    assert gated.facts_text.count(g.NOT_IN_SOURCES) >= 3
    assert any("source" in w for w in gated.warnings)


def test_names_are_checked_best_effort() -> None:
    answer = Answer(
        "Who signed?",
        "en",
        "",
        facts=[Fact("Marta Quiroga signed the agreement.", (1,))],
        sources=[source(1, "Clause 9. Signed by Dana Whitcombe.")],
    )
    gated = g.gate(answer)
    assert "Marta Quiroga" in gated.facts_text  # names warn, they are not removed
    assert any("Marta Quiroga" in w for w in gated.warnings)


def test_claim_score_below_half_warns() -> None:
    answer = Answer(
        "Fee?",
        "en",
        "",
        facts=[Fact("The fee is EUR 48,000.", (1,))],
        sources=[source(1, "The fee is EUR 48,000.")],
    )
    assert not g.gate(answer, claim_score=lambda _p, _h: 0.9).warnings
    assert any(
        "not clearly supported" in w
        for w in g.gate(answer, claim_score=lambda _p, _h: 0.2).warnings
    )


def test_not_found_passes_through() -> None:
    answer = Answer("Fee?", "en", "I could not find this in the documents.", not_found=True)
    assert g.gate(answer).text == "I could not find this in the documents."


def test_shown_sources_are_exactly_the_cited_ones() -> None:
    """T30.AC1 (gate side): every [n] in the shown text has a source entry, and only those."""
    answer = Answer(
        "Fee?",
        "en",
        "",
        facts=[Fact("The fee is EUR 48,000.", (1,))],
        sources=[source(1, "Clause 3. The fee is EUR 48,000."), source(2, "Other text.")],
    )
    gated = g.gate(answer)
    assert [s["n"] for s in gated.sources] == g.parse_citations(gated.text) == [1]
    assert gated.sources[0]["name"] == "1" and gated.sources[0]["excerpt"].startswith("Clause 3")


def test_locations_read_plainly() -> None:
    assert g.describe_location({"page": 2}) == "page 2"
    assert g.describe_location({"paragraph": 5, "clause": "3"}) == "clause 3, paragraph 5"
    assert g.describe_location({"cue": 9, "start": "00:00:52.000"}) == "at 00:00:52"
    assert g.describe_location({"cell": "Summary!A5:C5"}) == "cells Summary!A5:C5"
    assert g.describe_location({"image": True, "line": 3}) == "image, line 3"
