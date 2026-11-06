"""T46.AC2: the dependency policy applied to pip-audit results.

Fixtures: a vulnerability with a fixed version (open until upgraded), one fixed since the
previous scan (fixed), a valid High exception (accepted), an expired exception (open), an
advisory with no severity (counted as High, open without an exception), and scanner failures.
"""

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import check_audit_policy as policy  # noqa: E402

TODAY = date(2026, 11, 3)


def scan(*vulns: tuple[str, str, list[str]]) -> dict:
    """pip-audit's JSON: (package, advisory ID, fixed versions)."""
    deps: dict[str, list] = {}
    for package, advisory, fixes in vulns:
        deps.setdefault(package, []).append(
            {"id": advisory, "fix_versions": fixes, "aliases": [], "description": "x"}
        )
    return {
        "dependencies": [{"name": n, "version": "1.0", "vulns": v} for n, v in deps.items()]
        + [{"name": "clean", "version": "2.0", "vulns": []}]
    }


def severity(**levels: str | None) -> dict:
    return {
        k: {"severity": v, "source": "OSV", "retrieved": "2026-11-03"} for k, v in levels.items()
    }


EXCEPTION = {
    "id": "GHSA-none-high",
    "severity": "HIGH",
    "source": "OSV (2026-11-03)",
    "exposure": "only the export library, local files only",
    "owner": "the owner",
    "expires": "2026-12-31",
}


def test_statuses() -> None:
    now = scan(
        ("a", "GHSA-has-fix", ["1.1"]),
        ("b", "GHSA-none-high", []),
        ("c", "GHSA-expired", []),
        ("d", "GHSA-no-severity", []),
    )
    before = scan(("e", "GHSA-gone", ["2.0"]))
    levels = severity(
        **{
            "GHSA-has-fix": "LOW",
            "GHSA-none-high": "HIGH",
            "GHSA-expired": "MEDIUM",
            "GHSA-no-severity": None,
        }
    )
    exceptions = [
        EXCEPTION,
        {**EXCEPTION, "id": "GHSA-expired", "severity": "MEDIUM", "expires": "2026-11-01"},
    ]
    result = policy.evaluate(now, before, levels, exceptions, TODAY)
    status = {f["id"]: f["status"] for f in result}
    assert status == {
        "GHSA-has-fix": "open",
        "GHSA-none-high": "accepted_exception",
        "GHSA-expired": "open",
        "GHSA-no-severity": "open",
        "GHSA-gone": "fixed",
    }
    assert {f["id"]: f["severity"] for f in result}["GHSA-no-severity"] == "HIGH"


def test_an_exception_needs_every_field() -> None:
    now = scan(("b", "GHSA-none-high", []))
    incomplete = {k: v for k, v in EXCEPTION.items() if k != "exposure"}
    result = policy.evaluate(now, None, severity(**{"GHSA-none-high": "HIGH"}), [incomplete], TODAY)
    assert result[0]["status"] == "open"


def run(tmp_path: Path, audit: object, levels: object | None = None) -> int:
    (tmp_path / "pip_audit.json").write_text(audit if isinstance(audit, str) else json.dumps(audit))
    (tmp_path / "osv_severity.json").write_text(json.dumps(levels or {}))
    (tmp_path / "exceptions.toml").write_text("")
    return subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "check_audit_policy.py"),
            "--audit",
            str(tmp_path / "pip_audit.json"),
            "--severity",
            str(tmp_path / "osv_severity.json"),
            "--exceptions",
            str(tmp_path / "exceptions.toml"),
            "--out",
            str(tmp_path / "out.json"),
            "--today",
            "2026-11-03",
        ],
        capture_output=True,
        check=False,
    ).returncode


def test_exit_codes(tmp_path: Path) -> None:
    assert run(tmp_path, scan()) == 0
    assert run(tmp_path, scan(("a", "GHSA-x", ["1.1"])), severity(**{"GHSA-x": "LOW"})) == 1
    assert run(tmp_path, "{not json") == 2  # scanner failure, distinct from findings
    assert run(tmp_path, {"error": "network"}) == 2


@pytest.mark.parametrize("missing", ["GHSA-x"])
def test_unknown_severity_lookup_counts_as_high(missing: str) -> None:
    result = policy.evaluate(scan(("a", missing, [])), None, {}, [], TODAY)
    assert result[0]["severity"] == "HIGH" and result[0]["status"] == "open"
