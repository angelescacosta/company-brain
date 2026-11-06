"""T19 PBT: normalisation gives unit length, is idempotent, and rejects bad vectors.

Protects cosine ranking: every stored vector must have length 1.
"""

import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from company_brain.embedding import normalise

finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
vectors = st.lists(finite, min_size=1, max_size=64)


def norm(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


@given(vectors)
def test_unit_length_and_idempotent(vector: list[float]) -> None:
    assume(norm(vector) > 1e-6)
    once = normalise(vector)
    assert len(once) == len(vector)
    assert math.isclose(norm(once), 1.0, rel_tol=1e-9)
    twice = normalise(once)
    assert all(
        math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12) for a, b in zip(once, twice, strict=True)
    )


@given(st.integers(min_value=1, max_value=64))
def test_zero_vector_rejected(size: int) -> None:
    with pytest.raises(ValueError):
        normalise([0.0] * size)


@given(vectors, st.sampled_from([math.nan, math.inf, -math.inf]), st.data())
def test_non_finite_rejected(vector: list[float], bad: float, data: st.DataObject) -> None:
    position = data.draw(st.integers(min_value=0, max_value=len(vector) - 1))
    vector[position] = bad
    with pytest.raises(ValueError):
        normalise(vector)


def test_empty_rejected() -> None:
    with pytest.raises(ValueError):
        normalise([])
