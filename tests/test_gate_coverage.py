"""T25.AC6: every output path calls the gate. Answer mode now; later paths add their own."""

from company_brain import answer, guardrails
from company_brain.rerank import Selection
from company_brain.retrieval import Hit


def test_answer_mode_output_goes_through_the_gate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = []
    real = guardrails.gate

    def spy(result, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(result)
        return real(result, **kwargs)

    monkeypatch.setattr(guardrails, "gate", spy)
    hit = Hit("c1", "d1", "A", "A", "x/1", "The fee is EUR 48,000.", "label", None, (), 0.0)
    shown = answer.respond(
        "What is the fee?",
        Selection(kept=((hit, 1.0),)),
        lambda _m: (
            '{"facts": [{"text": "The fee is EUR 48,000.", "sources": [1]}], '
            '"suggestions": [], "questions": []}'
        ),
    )
    assert len(calls) == 1 and "EUR 48,000" in shown.text
    shown = answer.respond("What is the fee?", Selection(kept=(), not_found=True), lambda _m: "")
    assert len(calls) == 2


def test_draft_mode_output_goes_through_the_gate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """T25.AC6 for Draft mode (T27): every section and the questions pass the gate."""
    import json

    from company_brain import draft

    calls = []
    real = guardrails.gate

    def spy(result, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(result)
        return real(result, **kwargs)

    monkeypatch.setattr(guardrails, "gate", spy)
    hit = Hit("c1", "d1", "A", "A", "x/1", "The pilot covers two clinics.", "label", None, (), 0.0)
    empty = {"facts": [], "suggestions": []}
    output = {
        "background": empty,
        "objectives": empty,
        "measures": empty,
        "value": empty,
        "options": [],
        "retainer": {"suggestions": []},
        "questions": [],
    }
    draft.draft("Draft a proposal", Selection(kept=((hit, 1.0),)), lambda _m: json.dumps(output))
    assert len(calls) == 7  # four sections, options, retainer and the questions
