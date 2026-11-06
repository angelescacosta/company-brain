"""T17 PBT: the amount and date normaliser used to score OCR against facts.json.

For any amount with 2 decimals and any date, rendered with an explicit locale
(en-GB, en-US, es-ES) in a supported format, normalising with that locale gives
back the original value. Numeric dates that are ambiguous without a locale are
rejected, never guessed: 03/04 must never silently become the wrong month.
"""

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from company_brain.normalise import (
    LOCALES,
    AmbiguousDate,
    parse_amount,
    parse_date,
    render_amount,
    render_date,
)

amounts = st.decimals(min_value=0, max_value=Decimal("9999999.99"), places=2)
dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31))
locales = st.sampled_from(LOCALES)


@given(amounts, locales, st.booleans(), st.sampled_from(["", "EUR ", "GBP ", "£", "€"]))
def test_amount_round_trip(value: Decimal, locale: str, grouping: bool, prefix: str) -> None:
    assert parse_amount(prefix + render_amount(value, locale, grouping), locale) == value


@given(dates, locales, st.integers(0, 10))
def test_date_round_trip(day: date, locale: str, style: int) -> None:
    assert parse_date(render_date(day, locale, style), locale) == day


@given(st.integers(1, 12), st.integers(1, 12), st.integers(2000, 2099), st.sampled_from("/-."))
def test_ambiguous_numeric_dates_are_rejected_without_a_locale(
    a: int, b: int, year: int, sep: str
) -> None:
    text = f"{a:02d}{sep}{b:02d}{sep}{year}"
    if a == b:
        assert parse_date(text, None) == date(year, a, a)
    else:
        with pytest.raises(AmbiguousDate):
            parse_date(text, None)


def test_examples() -> None:
    assert parse_amount("52,30", "es-ES") == Decimal("52.30")
    assert parse_amount("1.234,56", "es-ES") == Decimal("1234.56")
    assert parse_amount("1,234.56", "en-GB") == Decimal("1234.56")
    assert parse_amount("abc", "en-GB") is None
    assert parse_date("03/04/2026", "en-GB") == date(2026, 4, 3)
    assert parse_date("03/04/2026", "en-US") == date(2026, 3, 4)
    assert parse_date("15 de octubre de 2026", "es-ES") == date(2026, 10, 15)
    assert parse_date("06 Oct 2026", "en-GB") == date(2026, 10, 6)
    assert parse_date("31/02/2026", "en-GB") is None
