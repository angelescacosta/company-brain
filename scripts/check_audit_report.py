"""T46.AC1 and AC3: completeness of `reports/security_audit.md`.

Every OWASP category has a verdict ("checked, no finding", "finding F-nn" or
"not applicable: reason") with evidence (a file and line, or a test selector).
Every finding has severity, rationale and status; Critical and High findings are
fixed, on the regression branch (fix commit, test selector, before and after
results) or the alternative branch (fix commit, justification, procedure, expected
result, evidence); Medium and Low findings are fixed or deferred with a reason and
a go-live reference.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

EVIDENCE = re.compile(r"[\w./-]+\.\w+:\d+|tests/[\w./-]+\.py(::[\w\[\]-]+)?")
SELECTOR = re.compile(r"^tests/[\w./-]+\.py(::[\w\[\]-]+)*$")
VERDICT = re.compile(r"^(checked, no finding|finding (F-\d+)(, F-\d+)*|not applicable: .{5,})$")
BRANCH_FIELDS = {
    "regression": ("Fix commit", "Test", "Before", "After"),
    "alternative": ("Fix commit", "Justification", "Procedure", "Expected", "Evidence"),
}


def categories(path: Path) -> dict[str, str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {cid: name for group in data.values() for cid, name in group.items()}


def _findings(text: str) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for block in re.split(r"^### ", text, flags=re.MULTILINE)[1:]:
        match = re.match(r"(F-\d+):", block)
        if not match:
            continue
        fields = dict(re.findall(r"^- ([A-Za-z -]+): (.+)$", block, flags=re.MULTILINE))
        found[match.group(1)] = {k.strip(): v.strip() for k, v in fields.items()}
    return found


def problems(text: str, wanted: dict[str, str]) -> list[str]:
    found: list[str] = []
    findings = _findings(text)
    rows = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 4 and cells[0] in wanted:
            rows[cells[0]] = cells
    for cid in wanted:
        if cid not in rows:
            found.append(f"{cid}: no verdict")
            continue
        _cid, _name, verdict, evidence = rows[cid]
        if not VERDICT.match(verdict):
            found.append(f"{cid}: unknown verdict {verdict!r}")
        for ref in re.findall(r"F-\d+", verdict):
            if ref not in findings:
                found.append(f"{cid}: names {ref}, which is not in the findings")
        if not EVIDENCE.search(evidence):
            found.append(f"{cid}: evidence must cite a file and line or a test selector")
    for fid, fields in findings.items():
        for name in ("Severity", "Rationale", "Status"):
            if not fields.get(name):
                found.append(f"{fid}: missing {name}")
        severity, status = fields.get("Severity", ""), fields.get("Status", "")
        if severity in ("Critical", "High"):
            if status != "fixed":
                found.append(f"{fid}: a {severity} finding must be fixed (status {status!r})")
            branch = fields.get("Branch", "")
            if branch not in BRANCH_FIELDS:
                found.append(f"{fid}: branch must be regression or alternative")
                continue
            for name in BRANCH_FIELDS[branch]:
                if not fields.get(name):
                    found.append(f"{fid}: missing {name}")
            if branch == "regression":
                if not SELECTOR.match(fields.get("Test", "")):
                    found.append(f"{fid}: the test must be a test selector")
                if not fields.get("Before", "").startswith("failed"):
                    found.append(f"{fid}: the test must have failed before the fix")
                if not fields.get("After", "").startswith("passed"):
                    found.append(f"{fid}: the test must pass after the fix")
        elif severity in ("Medium", "Low"):
            if status == "deferred":
                for name in ("Reason", "Go-live reference"):
                    if not fields.get(name):
                        found.append(f"{fid}: a deferred finding needs {name}")
            elif status != "fixed":
                found.append(f"{fid}: status must be fixed or deferred")
        else:
            found.append(f"{fid}: unknown severity {severity!r}")
    return found


def regression_selectors(text: str) -> list[str]:
    return [
        f["Test"]
        for f in _findings(text).values()
        if f.get("Branch") == "regression" and f.get("Status") == "fixed" and f.get("Test")
    ]


if __name__ == "__main__":
    repo = Path(__file__).resolve().parents[1]
    report = (repo / "reports" / "security_audit.md").read_text(encoding="utf-8")
    issues = problems(report, categories(repo / "config" / "owasp_categories.toml"))
    print("\n".join(issues) or "The audit report is complete.")
    sys.exit(1 if issues else 0)
