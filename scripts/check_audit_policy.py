"""T46.AC2: apply the dependency policy to pip-audit's results.

Statuses: `fixed` (in the previous scan, gone now), `accepted_exception` (no fixed version,
and a complete exception that has not expired), `open` (everything else: a fixed version
exists but is not installed, no exception, an incomplete or expired one). Severity comes
from the advisory record (OSV); an advisory with no severity counts as High.
Exit codes: 0 no open finding, 1 open findings, 2 the scan itself failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import date
from pathlib import Path
from typing import Any

EXCEPTION_FIELDS = ("id", "severity", "source", "exposure", "owner", "expires")


class ScanFailed(Exception):
    pass


def _vulns(scan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if scan is None:
        return {}
    if "error" in scan or not isinstance(scan.get("dependencies"), list):
        raise ScanFailed("the pip-audit result is not a complete scan")
    found = {}
    for dep in scan["dependencies"]:
        for vuln in dep.get("vulns", []):
            found[vuln["id"]] = {
                "package": dep["name"],
                "version": dep["version"],
                "fix_versions": list(vuln.get("fix_versions", [])),
            }
    return found


def _valid(exception: dict[str, Any] | None, today: date) -> bool:
    if exception is None or any(not str(exception.get(k, "")).strip() for k in EXCEPTION_FIELDS):
        return False
    try:
        return date.fromisoformat(str(exception["expires"])) >= today
    except ValueError:
        return False


def evaluate(
    now: dict[str, Any],
    before: dict[str, Any] | None,
    severities: dict[str, dict[str, Any]],
    exceptions: list[dict[str, Any]],
    today: date,
) -> list[dict[str, Any]]:
    current, earlier = _vulns(now), _vulns(before)
    by_id = {e.get("id"): e for e in exceptions}
    results = []
    for advisory, info in sorted(current.items()):
        record = severities.get(advisory) or {}
        level = str(record.get("severity") or "HIGH").upper()
        if info["fix_versions"]:
            status = "open"  # a fixed version exists: upgrade
        elif _valid(by_id.get(advisory), today):
            status = "accepted_exception"
        else:
            status = "open"
        results.append(
            {
                "id": advisory,
                **info,
                "severity": level,
                "severity_source": record.get("source", "none: counted as High"),
                "retrieved": record.get("retrieved", ""),
                "status": status,
            }
        )
    for advisory, info in sorted(earlier.items()):
        if advisory not in current:
            results.append({"id": advisory, **info, "severity": "", "status": "fixed"})
    return results


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--severity", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--today", default=date.today().isoformat())
    args = parser.parse_args(argv)
    try:
        now = json.loads(args.audit.read_text(encoding="utf-8"))
        before = (
            json.loads(args.before.read_text(encoding="utf-8"))
            if args.before and args.before.exists()
            else None
        )
        severities = json.loads(args.severity.read_text(encoding="utf-8"))
        exceptions = tomllib.loads(args.exceptions.read_text(encoding="utf-8")).get("exception", [])
        results = evaluate(now, before, severities, exceptions, date.fromisoformat(args.today))
    except (OSError, ValueError, ScanFailed, KeyError) as error:
        print(f"Scan failed: {error}")
        return 2
    args.out.write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    open_ = [r for r in results if r["status"] == "open"]
    for r in results:
        print(f"{r['status']:<20} {r['id']} {r['package']} {r['version']} {r.get('severity', '')}")
    return 1 if open_ else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
