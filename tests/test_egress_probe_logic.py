"""T4.AC3 PBT: the firewall probe verdict.

For any combination of probe and control outcomes, the verdict is "pass" only
when at least one probe applied and every applicable probe was blocked, with
LuLu's own deny record, while its control reached the internet. A probe whose
control failed is "not applicable", never a pass.
"""

from hypothesis import given
from hypothesis import strategies as st

from company_brain.firewall import ProbeResult, probe_outcome, verdict

results = st.builds(
    ProbeResult,
    name=st.sampled_from(["ollama-pull", "python-ipv4", "python-ipv6", "child-ipv4"]),
    control_ok=st.booleans(),
    connected=st.booleans(),
    deny_recorded=st.booleans(),
)


@given(st.lists(results, max_size=8))
def test_verdict_rule(probes: list[ProbeResult]) -> None:
    applicable = [p for p in probes if p.control_ok]
    expected_pass = bool(applicable) and all(
        not p.connected and p.deny_recorded for p in applicable
    )
    result = verdict(probes)
    assert (result == "pass") == expected_pass
    if any(p.control_ok and p.connected for p in probes):
        assert result == "fail"
    if not applicable:
        assert result == "inconclusive"


@given(results)
def test_single_probe_outcome(probe: ProbeResult) -> None:
    outcome = probe_outcome(probe)
    if not probe.control_ok:
        assert outcome == "not applicable"
    elif probe.connected:
        assert outcome == "fail"
    elif probe.deny_recorded:
        assert outcome == "pass"
    else:
        assert outcome == "inconclusive"
