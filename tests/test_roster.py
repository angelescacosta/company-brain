"""T7.AC2 PBT (revision 9): the memory-pressure verdict and the one-model roster.

For any pressure trace, the verdict is "fail" exactly when a critical reading
exists, "inconclusive" exactly when there is no critical reading but a reading
is missing, the trace is empty or two samples are more than 2 s apart, and
"pass" otherwise; memory and swap never change it. The roster is the answering
model exactly when its run passed. This keeps a missed or unreadable spike
from counting as a pass (review 9).
"""

from hypothesis import given
from hypothesis import strategies as st

from company_brain.benchmark import (
    ANSWER_MODEL,
    CRITICAL,
    MAX_GAP_S,
    NORMAL,
    WARNING,
    BenchmarkRow,
    flagged,
    pressure_verdict,
    roster,
    roster_path,
)

levels = st.one_of(st.none(), st.sampled_from([NORMAL, WARNING, CRITICAL]))
steps = st.floats(0.01, 3.0, allow_nan=False)
traces = st.lists(st.tuples(steps, levels), max_size=40).map(
    lambda items: [
        (sum(s for s, _ in items[: i + 1]), level) for i, (_s, level) in enumerate(items)
    ]
)


@given(traces)
def test_verdict_rule(trace: list[tuple[float, int | None]]) -> None:
    times = [t for t, _ in trace]
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    readings = [level for _, level in trace]
    verdict = pressure_verdict(trace)
    if CRITICAL in readings:
        assert verdict == "fail"
    elif not trace or None in readings or any(g > MAX_GAP_S for g in gaps):
        assert verdict == "inconclusive"
    else:
        assert verdict == "pass"


rows = st.builds(
    BenchmarkRow,
    model=st.just(ANSWER_MODEL),
    baseline_memory=st.integers(0, 16_000_000_000),
    peak_memory=st.integers(0, 17_000_000_000),
    swap_growth=st.integers(0, 9_000_000_000),
    first_token_s=st.floats(0, 100),
    total_s=st.floats(0, 600),
    peak_pressure=st.sampled_from([NORMAL, WARNING, CRITICAL]),
    pressure_samples=st.integers(0, 10_000),
    longest_gap_s=st.floats(0, 10),
    pressure_verdict=st.sampled_from(["pass", "fail", "inconclusive"]),
)


@given(st.lists(rows, max_size=1))
def test_roster_is_the_answering_model_exactly_on_pass(table: list[BenchmarkRow]) -> None:
    chosen = roster(table)
    passed = [r for r in table if r.pressure_verdict == "pass"]
    assert chosen == ([ANSWER_MODEL] if passed else [])
    assert all(flagged(r) == (r.pressure_verdict != "pass") for r in table)
    assert roster_path(chosen) == ("single-model" if passed else "blocked")
