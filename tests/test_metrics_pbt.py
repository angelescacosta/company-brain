"""T34 PBT and hand-computed examples for the evaluation metrics.

Recall@5 and MRR stay in [0, 1]; MRR is 1 exactly when the first chunk covers
a required span; reordering chunks below the first relevant one leaves MRR
unchanged; fact matching does not depend on how a value is written within the
grammar. These protect the scoreboard from silently wrong numbers.
"""

from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from company_brain import evaluation as ev

SPAN = "Ravi Senthil: Agreed: the first phase covers the Solihull and Dudley clinics."
OTHER = "Hannah Greaves: Reminders go out by post in two clinics."
chunks = st.lists(st.sampled_from([SPAN, OTHER, "unrelated text " * 5, SPAN[:40]]), max_size=12)


def test_hand_computed_examples() -> None:
    """T34.AC1."""
    assert ev.covers(f"intro\n{SPAN}\noutro", SPAN)
    assert ev.covers(SPAN[: int(len(SPAN) * 0.6)], SPAN)
    assert not ev.covers(SPAN[: int(len(SPAN) * 0.4)], SPAN)
    ranked = [[OTHER, SPAN], [SPAN], [OTHER], [OTHER, OTHER, OTHER, OTHER, OTHER, SPAN]]
    spans = [[SPAN], [SPAN], [SPAN], [SPAN]]
    assert ev.recall_at_k(spans, ranked, 5) == 0.5
    assert ev.mrr(spans, ranked) == (1 / 2 + 1 + 0 + 1 / 6) / 4
    assert ev.evidence_coverage([[SPAN]], [[OTHER]], [[SPAN]]) == 1.0
    assert ev.percent(33, 44) == "75% (33 of 44)"
    assert ev.percent(9, 11).endswith("(9 of 11, small sample)")


@given(st.lists(chunks, min_size=1, max_size=6))
def test_recall_and_mrr_are_between_zero_and_one(ranked: list[list[str]]) -> None:
    spans = [[SPAN] for _ in ranked]
    assert 0 <= ev.recall_at_k(spans, ranked, 5) <= 1
    assert 0 <= ev.mrr(spans, ranked) <= 1


@given(chunks)
def test_mrr_is_one_exactly_when_the_first_chunk_covers(ranked: list[str]) -> None:
    value = ev.mrr([[SPAN]], [ranked])
    assert (value == 1) == (bool(ranked) and ev.covers(ranked[0], SPAN))


@given(chunks, st.randoms())
def test_reordering_below_the_first_relevant_chunk_keeps_mrr(ranked: list[str], rnd) -> None:  # type: ignore[no-untyped-def]
    first = next((i for i, c in enumerate(ranked) if ev.covers(c, SPAN)), None)
    if first is None:
        return
    tail = ranked[first + 1 :]
    rnd.shuffle(tail)
    assert ev.mrr([[SPAN]], [ranked]) == ev.mrr([[SPAN]], [ranked[: first + 1] + tail])


AMOUNTS = st.decimals(min_value=Decimal("100"), max_value=Decimal("999999"), places=0)


@given(
    AMOUNTS, st.sampled_from(["EUR {en}", "{en} EUR", "€{en}", "{es} €", "{es} EUR", "{en} euros"])
)
def test_fact_matching_ignores_value_formatting(value: Decimal, style: str) -> None:
    en = f"{value:,.0f}"
    es = en.replace(",", ".")
    fact = {
        "type": "value",
        "attribute": "fee",
        "value": int(value),
        "unit": "EUR",
        "text": f"EUR {en}",
    }
    answer = "The agreed fee is " + style.format(en=en, es=es) + ". [1]"
    assert ev.fact_correct(fact, answer, "en")
    assert not ev.fact_correct(
        {**fact, "value": int(value) + 1, "text": f"EUR {int(value) + 1:,}"}, answer, "en"
    )


def test_actions_need_the_same_owner() -> None:
    fact = {
        "type": "action",
        "action": "export twelve months of booking data",
        "owner": "Hannah Greaves",
        "due": "2026-09-04",
    }
    assert ev.fact_correct(
        fact,
        "Action: export twelve months of booking data "
        "(owner Hannah Greaves, due 4 September 2026). [2]",
        "en",
    )
    assert not ev.fact_correct(
        fact, "Action: export twelve months of booking data (owner Ravi Senthil). [2]", "en"
    )


def test_hard_blockers_fail_the_run_whatever_the_averages() -> None:
    """T34.AC2."""
    clean = ev.QuestionResult("q1", "en", "fact", True, 1.0, True, [], [])
    leak = ev.QuestionResult("q2", "en", "fact", True, 1.0, True, ["another client: B"], [])
    unsourced = ev.QuestionResult(
        "q3", "en", "fact", True, 1.0, True, [], ["EUR 9,999 is not in the cited sources"]
    )
    assert ev.blockers([clean] * 50) == []
    assert ev.blockers([clean] * 50 + [leak]) and ev.blockers([clean] * 50 + [unsourced])


def test_found_problems_in_the_first_run() -> None:
    """Evaluator mistakes seen in the first tuning run (2026-10-24); each was a correct answer."""
    start = {
        "type": "value",
        "attribute": "start date",
        "value": "2026-09-14",
        "unit": "date",
        "text": "14 September 2026",
    }
    assert ev.fact_correct(start, "The work starts on 14 September 2026. [1]", "en")
    assert not ev.fact_correct(start, "The work starts on 15 September 2026. [1]", "en")
    receipt = {
        "type": "receipt",
        "fields": {
            "vendor": "Brindlewood Print",
            "date": "2026-10-13",
            "total": "86.40",
            "vat": "14.40",
            "currency": "GBP",
        },
    }
    assert ev.fact_correct(receipt, "The total was GBP 86.40, including GBP 14.40 VAT. [1]", "en")
    spoken_es = {
        "type": "action",
        "action": "enviar las tarifas actuales de los cuatro hoteles",
        "owner": "Lucía Ferrer",
        "due": "2026-09-07",
    }
    english = "Lucía Ferrer sends the current rates of the four hotels by September 7, 2026. [1]"
    assert ev.fact_correct(spoken_es, english, "en", mode="answer")
    assert not ev.fact_correct(
        spoken_es, "Tom Hartley sends them by September 7, 2026.", "en", mode="answer"
    )
    decision = {"type": "decision", "decision": "Durham and Lincoln join in January 2027"}
    assert ev.fact_correct(
        decision, "Durham and Lincoln are scheduled to join the program in January 2027. [1]", "en"
    )
    assert not ev.fact_correct(decision, "Durham joins in March 2027. [1]", "en")
