"""T4.AC3: the real blocked programs cannot reach the internet while the owner's
curl, to the same address family and destination, can (probes in
`company_brain.egress`, D28). Read-only, listed in config/real_resource_allowlist.toml.
"""

import json

import pytest

from company_brain.egress import run_probes
from company_brain.firewall import probe_outcome, verdict
from company_brain.isolation import real_resources

pytestmark = pytest.mark.integration


def test_blocked_programs_cannot_reach_the_internet() -> None:
    with real_resources("network-probe"):
        probes = run_probes("127.0.0.1:11434")
    report = {p.name: probe_outcome(p) for p in probes}
    print(json.dumps(report, indent=1))
    assert verdict(probes) == "pass", report
    assert report["ollama-pull"] == "pass" and report["python-ipv4"] == "pass", report
