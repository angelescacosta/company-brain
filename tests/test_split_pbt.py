"""T14.AC3 PBT: the tuning/locked split.

For any labelled set with at least three clients per language and every
category in every client, the split is disjoint by client, covers every
question exactly once, has every category in both parts for each language,
locks exactly one client per language, and is the same for the same seed.
Fewer than three clients in a language, or a category missing from a client
group, raises. This protects the locked set from leaking into tuning.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from company_brain.questions import CATEGORIES, Infeasible, split


def labelled(spec: dict[str, list[list[str]]]) -> list[dict]:
    out = []
    for language, clients in spec.items():
        for c, categories in enumerate(clients):
            for k, category in enumerate(categories):
                out.append(
                    {
                        "id": f"{language}-{c}-{k}",
                        "language": language,
                        "client": f"{language}-client-{c}",
                        "category": category,
                    }
                )
    return out


full_client = st.lists(st.sampled_from(CATEGORIES), max_size=4).map(
    lambda extra: [*CATEGORIES, *extra]
)
feasible = st.fixed_dictionaries(
    {
        "en": st.lists(full_client, min_size=3, max_size=5),
        "es": st.lists(full_client, min_size=3, max_size=5),
    }
)


@given(feasible, st.integers(0, 2**32))
def test_split_properties(spec: dict[str, list[list[str]]], seed: int) -> None:
    items = labelled(spec)
    tuning, locked = split(items, seed)
    assert sorted(q["id"] for q in tuning + locked) == sorted(q["id"] for q in items)
    assert not {q["client"] for q in tuning} & {q["client"] for q in locked}
    for language in ("en", "es"):
        assert len({q["client"] for q in locked if q["language"] == language}) == 1
        for part in (tuning, locked):
            assert {q["category"] for q in part if q["language"] == language} == set(CATEGORIES)
    assert split(items, seed) == (tuning, locked)


@given(st.lists(full_client, min_size=1, max_size=2), st.integers(0, 2**32))
def test_too_few_clients_raises(clients: list[list[str]], seed: int) -> None:
    items = labelled({"en": clients, "es": [list(CATEGORIES)] * 3})
    with pytest.raises(Infeasible):
        split(items, seed)


@given(st.sampled_from(CATEGORIES), st.integers(0, 2**32))
def test_missing_category_raises(missing: str, seed: int) -> None:
    partial = [c for c in CATEGORIES if c != missing]
    items = labelled({"en": [partial] * 3, "es": [list(CATEGORIES)] * 3})
    with pytest.raises(Infeasible):
        split(items, seed)
