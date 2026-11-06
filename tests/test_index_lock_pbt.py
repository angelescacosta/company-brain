"""T20U PBT: the coordination policy for updates, backups, stops and crashes.

For any interleaving of start, stop, update and backup requests and holder
crashes: at most one holder at a time; services are never stopped under a
holder; every request ends as run, waited-then-run, "already updating" or
skipped with a reason; a crashed holder never blocks forever (the operating
system releases its lock). Protects the index and the backups from concurrent
writers.
"""

from hypothesis import given
from hypothesis import strategies as st

from company_brain.coordinator import OUTCOMES, Simulation

events = st.lists(
    st.sampled_from(["update", "backup", "stop", "start", "crash", "finish"]), max_size=40
)


@given(events)
def test_policy_invariants(sequence: list[str]) -> None:
    sim = Simulation()
    for event in sequence:
        sim.step(event)
        assert len(sim.holders) <= 1
        assert not (sim.holders and sim.stopped_under_holder)
    sim.drain()
    assert sim.holders == []
    assert all(outcome in OUTCOMES for outcome in sim.outcomes)
    assert len(sim.outcomes) == sum(1 for e in sequence if e in ("update", "backup", "stop"))
