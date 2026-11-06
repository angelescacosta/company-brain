"""T1 smoke PBT: the guard's path classification.

Protects against a real folder being mistaken for a test folder, or the other
way round.
"""

from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from company_brain.isolation import RealResources

segment = st.text(
    alphabet=st.characters(categories=["L", "N"], include_characters="-_ ."),
    min_size=1,
    max_size=12,
).filter(lambda s: s not in {".", ".."})

HOME = Path("/Users/someone")
RESOURCES = RealResources.for_home(HOME)


@given(st.lists(segment, max_size=4))
def test_paths_under_real_data_are_real(parts: list[str]) -> None:
    assert RESOURCES.kind_of_path(HOME.joinpath("CompanyBrainData", *parts)) == "data-dir"


@given(st.lists(segment, min_size=1, max_size=4))
def test_paths_under_temp_are_not_real(parts: list[str]) -> None:
    path = Path("/private/var/folders/cb-test-x").joinpath(*parts)
    assert RESOURCES.kind_of_path(path) is None
    assert RESOURCES.kinds_in_text(f"cat {path}") == set()


@given(segment)
def test_sibling_names_are_not_real(suffix: str) -> None:
    assert RESOURCES.kind_of_path(HOME / f"CompanyBrainData{suffix}x") is None
