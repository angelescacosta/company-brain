"""T8.AC1 PBT: a record is accepted exactly when every field is allowlisted and typed.

Protects against confidential text reaching the logs Claude reads. Validity
is decided by construction (which generator produced each value), not by
re-running the logger's own checks.
"""

import io
import math

from hypothesis import given
from hypothesis import strategies as st

from company_brain.logs import (
    EVENTS,
    FIELDS,
    EnumField,
    FloatField,
    IdField,
    IntField,
    LogRecordRejected,
    StructuredLogger,
)

free_text = st.text(min_size=1, max_size=40).map(lambda s: s + " text")


def valid_value(spec: object) -> st.SearchStrategy[object]:
    if isinstance(spec, IntField):
        return st.integers(min_value=-(10**12), max_value=10**12)
    if isinstance(spec, FloatField):
        return st.floats(allow_nan=False, allow_infinity=False, width=64)
    if isinstance(spec, EnumField):
        return st.sampled_from(sorted(spec.values))
    if isinstance(spec, IdField):
        return st.from_regex(spec.pattern, fullmatch=True)
    raise AssertionError(spec)


def invalid_value(spec: object) -> st.SearchStrategy[object]:
    if isinstance(spec, IntField):
        return st.one_of(st.booleans(), st.floats(), free_text, st.none())
    if isinstance(spec, FloatField):
        return st.one_of(
            st.sampled_from([math.nan, math.inf, -math.inf]), free_text, st.booleans(), st.none()
        )
    if isinstance(spec, EnumField):
        return st.one_of(free_text.filter(lambda s: s not in spec.values), st.integers())
    if isinstance(spec, IdField):
        return st.one_of(free_text, st.integers(), st.none())
    raise AssertionError(spec)


@st.composite
def records(draw: st.DrawFn) -> tuple[dict[str, object], bool]:
    names = draw(st.lists(st.sampled_from(sorted(FIELDS)), unique=True, max_size=6))
    fields: dict[str, object] = {}
    ok = True
    for name in names:
        if draw(st.booleans()):
            fields[name] = draw(valid_value(FIELDS[name]))
        else:
            fields[name] = draw(invalid_value(FIELDS[name]))
            ok = False
    for name in draw(st.lists(st.text(min_size=1, max_size=12), max_size=2)):
        if name not in FIELDS and name not in fields:
            fields[name] = draw(st.integers())
            ok = False
    return fields, ok


@given(st.sampled_from(sorted(EVENTS)), records())
def test_accepted_exactly_when_allowlisted_and_typed(
    event: str, record: tuple[dict[str, object], bool]
) -> None:
    fields, ok = record
    stream = io.StringIO()
    try:
        StructuredLogger(stream).log(event, **fields)
        accepted = True
    except LogRecordRejected:
        accepted = False
    assert accepted == ok
    assert (stream.getvalue() != "") == ok
