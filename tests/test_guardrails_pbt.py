"""T25 PBT: the value check and the citation grammar.

For any generated source and answer within the value grammar: an injected
value that is absent from the cited sources is always caught (AC1); a present
value rewritten in another format of the grammar passes (AC2); values outside
the grammar come back "unchecked", never passed (AC3); and any citation string
round-trips while citation markers and list numbers are never read as values
(AC4). These protect against invented fees and against false alarms.
"""

from datetime import date
from decimal import Decimal

from hypothesis import assume, given
from hypothesis import strategies as st

from company_brain import guardrails as g

money = st.decimals(min_value=Decimal("1"), max_value=Decimal("999999"), places=0)
cents = st.decimals(min_value=Decimal("0.01"), max_value=Decimal("9999.99"), places=2)
days = st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31))


def amount_formats(value: Decimal) -> list[str]:
    whole = value == value.to_integral()
    en = f"{value:,.0f}" if whole else f"{value:,.2f}"
    es = en.replace(",", "\0").replace(".", ",").replace("\0", ".")
    return [f"EUR {en}", f"€{en}", f"{es} EUR", f"{es} €", f"{en} euros"]


def date_formats(day: date) -> list[str]:
    en = g.MONTHS["en"][day.month - 1].title()
    es = g.MONTHS["es"][day.month - 1]
    return [
        day.isoformat(),
        f"{day.day} {en} {day.year}",
        f"{day.day} de {es} de {day.year}",
        f"{en} {day.day}, {day.year}",
    ]


@given(money, money, st.integers(0, 4), st.integers(0, 4))
def test_absent_amount_is_always_caught(
    present: Decimal, injected: Decimal, a: int, b: int
) -> None:
    """AC1."""
    assume(injected not in g.interpretations(f"{present:,.0f}") | g.interpretations(f"{present}"))
    assume(injected * 1000 != present and present * 1000 != injected)
    source = f"Clause 3. The fee is {amount_formats(present)[a]}, payable in two parts."
    fact = f"The fee is {amount_formats(injected)[b]}."
    result = g.check_fact(fact, [1], {1: source}, language="en")
    assert result.failed_values, (source, fact)
    assert g.NOT_IN_SOURCES in result.text


@given(money, st.integers(0, 4), st.integers(0, 4))
def test_present_amount_in_another_format_passes(value: Decimal, a: int, b: int) -> None:
    """AC2."""
    source = f"The agreed fee is {amount_formats(value)[a]}."
    fact = f"They agreed {amount_formats(value)[b]}."
    result = g.check_fact(fact, [1], {1: source}, language="en")
    assert not result.failed_values and not result.unchecked, (source, fact)


@given(days, st.integers(0, 3), st.integers(0, 3))
def test_dates_in_any_grammar_format(day: date, a: int, b: int) -> None:
    """AC2 for dates, and AC1: another day is caught."""
    source = f"The agreement starts on {date_formats(day)[a]}."
    assert not g.check_fact(
        f"It starts on {date_formats(day)[b]}.", [1], {1: source}, "en"
    ).failed_values
    other = date(day.year, day.month, 28 if day.day != 28 else 27)
    assert g.check_fact(
        f"It starts on {date_formats(other)[b]}.", [1], {1: source}, "en"
    ).failed_values


@given(cents, st.sampled_from(["en", "es"]))
def test_percentages_and_decimals(value: Decimal, language: str) -> None:
    text = f"{value}%" if language == "en" else f"{str(value).replace('.', ',')} %"
    source = f"The share rose to {text}."
    assert not g.check_fact(f"It is now {text}.", [1], {1: source}, language).failed_values


OUT_OF_GRAMMAR = [
    "forty-eight thousand euros",
    "next Tuesday",
    "the day after tomorrow",
    "cuarenta y ocho mil",
    "la semana que viene",
    "03/04/2026",
    "mañana",
    "ten days",
]


@given(st.sampled_from(OUT_OF_GRAMMAR))
def test_out_of_grammar_values_are_unchecked(phrase: str) -> None:
    """AC3."""
    result = g.check_fact(f"The payment is due {phrase}.", [1], {1: "Payment terms apply."}, "en")
    assert result.unchecked and not result.passed_values


@given(st.lists(st.integers(1, 99), min_size=1, max_size=5, unique=True))
def test_citation_grammar_round_trips(numbers: list[int]) -> None:
    """AC4."""
    text = "The fee is fixed. " + g.render_citations(numbers)
    assert g.parse_citations(text) == numbers
    assert g.values("1. The fee is fixed " + g.render_citations(numbers)) == []
