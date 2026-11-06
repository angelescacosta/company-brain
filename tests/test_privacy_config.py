"""T4.AC1: the Open WebUI env file has every required setting with the required value.

PBT: any generated env file with a required key missing or weakened fails
validation; protects against a silently weakened configuration.
"""

from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from company_brain.privacy_config import ENV_FILE, REQUIRED, problems

REPO = Path(__file__).resolve().parents[1]


def render(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"{key}={value}" for key, value in pairs) + "\n"


def test_repository_env_file_is_valid() -> None:
    assert problems((REPO / ENV_FILE).read_text()) == []


def test_covers_the_prd_settings() -> None:
    for key in (
        "OFFLINE_MODE",
        "ENABLE_OTEL",
        "ANONYMIZED_TELEMETRY",
        "SCARF_NO_ANALYTICS",
        "ENABLE_TITLE_GENERATION",
        "ENABLE_TAGS_GENERATION",
        "ENABLE_FOLLOW_UP_GENERATION",
        "ENABLE_WEB_SEARCH",
        "ENABLE_CODE_EXECUTION",
        "ENABLE_SIGNUP",
        "AUDIT_LOG_LEVEL",
        "HF_HUB_OFFLINE",
        "HOST",
    ):
        assert key in REQUIRED, key
    assert REQUIRED["HOST"] == "127.0.0.1"


weakened_values = st.text(
    alphabet=st.characters(categories=["L", "N"], include_characters="._-"), max_size=10
)


@st.composite
def env_files(draw: st.DrawFn) -> tuple[str, bool]:
    pairs = list(REQUIRED.items())
    ok = True
    action = draw(st.sampled_from(["none", "drop", "weaken", "duplicate"]))
    if action != "none":
        key = draw(st.sampled_from(sorted(REQUIRED)))
        bad = draw(weakened_values.filter(lambda v: v != REQUIRED[key]))
        ok = False
        if action == "drop":
            pairs = [p for p in pairs if p[0] != key]
        elif action == "weaken":
            pairs = [(k, bad if k == key else v) for k, v in pairs]
        else:
            pairs.append((key, bad))
    extras = draw(st.lists(st.sampled_from(["# comment", "", "OTHER_SETTING=1"]), max_size=3))
    lines = [f"{k}={v}" for k, v in draw(st.permutations(pairs))] + extras
    return "\n".join(lines) + "\n", ok


@given(env_files())
def test_missing_or_weakened_key_fails(case: tuple[str, bool]) -> None:
    text, ok = case
    assert (problems(text) == []) == ok


def test_quotes_and_spaces_are_read() -> None:
    text = render(list(REQUIRED.items())).replace("HOST=127.0.0.1", 'HOST = "127.0.0.1"')
    assert problems(text) == []


def test_problem_names_the_key() -> None:
    text = render([(k, v) for k, v in REQUIRED.items() if k != "ENABLE_SIGNUP"])
    assert any("ENABLE_SIGNUP" in p for p in problems(text))
