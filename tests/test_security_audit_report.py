"""T46.AC1 and AC3: the audit report is complete (a verdict with evidence for every OWASP
category; every finding with the fields of its branch), and the regression tests of fixed
Critical or High findings pass. Whether a verdict is right is a review judgement; this
checks completeness and runs the linked tests."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import check_audit_report as checker  # noqa: E402

CATEGORIES = checker.categories(REPO / "config" / "owasp_categories.toml")


def verdicts(skip: str = "", evidence: str = "src/company_brain/backend.py:120") -> str:
    rows = [
        f"| {cid} | {name} | checked, no finding | {evidence} |"
        for cid, name in CATEGORIES.items()
        if cid != skip
    ]
    return "## Verdicts\n\n| ID | Category | Verdict | Evidence |\n|---|---|---|---|\n" + "\n".join(
        rows
    )


REGRESSION = """
### F-01: Download path escapes the export folder
- Severity: High
- Rationale: any chat could read files outside the exports folder
- Status: fixed
- Branch: regression
- Fix commit: 1234abc
- Test: tests/test_backend.py::test_download
- Before: failed (1 failed)
- After: passed (1 passed)
"""
ALTERNATIVE = """
### F-02: Firewall rule missing for a new interpreter
- Severity: Critical
- Rationale: the AI could reach the internet
- Status: fixed
- Branch: alternative
- Fix commit: 5678def
- Justification: needs the real LuLu firewall, which tests cannot change
- Procedure: run Check health after removing the rule in a test account
- Expected: Start AI refuses to start
- Evidence: reports/security/f02.txt
"""
DEFERRED = """
### F-03: Verbose error text in a log
- Severity: Low
- Rationale: local log only
- Status: deferred
- Reason: no confidential data in test mode
- Go-live reference: go-live PRD open list item 3
"""


def report(*parts: str) -> str:
    return "# Security audit\n\n" + verdicts() + "\n\n## Findings\n" + "".join(parts)


def test_a_complete_report_passes() -> None:
    assert checker.problems(report(REGRESSION, ALTERNATIVE, DEFERRED), CATEGORIES) == []


def test_malformed_reports_fail() -> None:
    missing = "# Security audit\n\n" + verdicts(skip="LLM07:2025") + "\n\n## Findings\n"
    assert any("LLM07:2025" in p for p in checker.problems(missing, CATEGORIES))
    no_evidence = "# Security audit\n\n" + verdicts(evidence="looked at it") + "\n\n## Findings\n"
    assert checker.problems(no_evidence, CATEGORIES)
    assert checker.problems(
        report(REGRESSION.replace("- Before: failed (1 failed)\n", "")), CATEGORIES
    )
    assert checker.problems(report(ALTERNATIVE.replace("- Procedure:", "- Steps:")), CATEGORIES)
    assert checker.problems(
        report(REGRESSION.replace("Status: fixed", "Status: deferred")), CATEGORIES
    )  # High findings cannot be deferred
    assert checker.problems(
        report(DEFERRED.replace("- Go-live reference: go-live PRD open list item 3\n", "")),
        CATEGORIES,
    )
    bad_verdict = report().replace("checked, no finding", "fine", 1)
    assert checker.problems(bad_verdict, CATEGORIES)
    dangling = report().replace("checked, no finding", "finding F-09", 1)
    assert checker.problems(dangling, CATEGORIES)


REAL = REPO / "reports" / "security_audit.md"


@pytest.mark.skipif(not REAL.exists(), reason="the audit report is written by T46")
def test_the_real_report_is_complete_and_its_tests_pass() -> None:
    text = REAL.read_text(encoding="utf-8")
    assert checker.problems(text, CATEGORIES) == []
    selectors = checker.regression_selectors(text)
    if selectors:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *selectors],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stdout[-2000:]
