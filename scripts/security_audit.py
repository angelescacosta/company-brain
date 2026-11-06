"""T46: the automated parts of the security audit; results go to `reports/security/`.

Preflight (recorded): the scanners' interpreter path, its version (3.10 or newer), that it
is not the AI services' interpreter, and that one advisory lookup succeeds. Then the
dependency scan (pip-audit on `uv export --locked`, local or editable entries excluded by
name and listed), the advisory severities from OSV, the AC2 policy, and the secret scan.
Records the git commit, the `uv.lock` hash, tool versions and the scan date.
Exit codes: 0 nothing open, 1 open findings, 2 a scanner or setup error.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tomllib
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "reports" / "security"
UV = str(Path.home() / ".local" / "bin" / "uv")
UVX = str(Path.home() / ".local" / "bin" / "uvx")
OSV = "https://api.osv.dev/v1/vulns/"
KNOWN_ADVISORY = "GHSA-9wx4-h78v-vm56"  # requests 2.31 (CVE-2024-35195): a lookup that must work


class SetupError(Exception):
    pass


def run(*args: str, cwd: Path = REPO) -> str:
    try:
        return subprocess.run(  # noqa: S603 - fixed programs
            list(args), cwd=cwd, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise SetupError(f"{Path(args[0]).name} failed: {type(error).__name__}") from None


def osv(advisory: str) -> dict[str, Any]:
    with urllib.request.urlopen(OSV + advisory, timeout=30) as response:  # noqa: S310 - fixed https
        return dict(json.load(response))


def level(record: dict[str, Any]) -> str | None:
    """The advisory's own severity (GitHub's label), else none (the policy counts it as High)."""
    label = (record.get("database_specific") or {}).get("severity")
    if label:
        return {"MODERATE": "MEDIUM"}.get(str(label).upper(), str(label).upper())
    for affected in record.get("affected", []):
        label = (affected.get("database_specific") or {}).get("severity")
        if label:
            return {"MODERATE": "MEDIUM"}.get(str(label).upper(), str(label).upper())
    return None


def preflight(tools: dict[str, Any]) -> dict[str, Any]:
    python = run(UV, "python", "find", tools["python"]).strip()
    version = run(python, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])").strip()
    ai = os.path.realpath(REPO / ".venv" / "bin" / "python")
    major, minor = (int(x) for x in version.split(".")[:2])
    if (major, minor) < (3, 10):
        raise SetupError(f"the tools' Python is {version}; 3.10 or newer is needed")
    if os.path.realpath(python) == ai:
        raise SetupError("the tools' Python is the AI services' Python")
    try:
        lookup = osv(KNOWN_ADVISORY).get("id") == KNOWN_ADVISORY
    except OSError as error:
        raise SetupError(f"the advisory lookup failed ({type(error).__name__})") from None
    if not lookup:
        raise SetupError("the advisory lookup returned the wrong record")
    return {
        "python": os.path.realpath(python),
        "version": version,
        "ai_python": ai,
        "advisory_lookup": "ok",
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tools = tomllib.loads((REPO / "config" / "dev_tools.toml").read_text(encoding="utf-8"))
    record: dict[str, Any] = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "commit": run("git", "rev-parse", "HEAD").strip(),
        "uv_lock_sha256": hashlib.sha256((REPO / "uv.lock").read_bytes()).hexdigest(),
        "tools": {k: v for k, v in tools.items()},
    }
    try:
        record["preflight"] = preflight(tools)
        exported = run(
            UV,
            "export",
            "--locked",
            "--all-groups",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--no-hashes",
        )
        kept, local = [], []
        for line in exported.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or line.startswith(" "):
                continue
            if stripped.startswith(("-e ", "./", "../", "/")) or " @ file:" in stripped:
                local.append(stripped)
            else:
                kept.append(stripped)
        record["local_entries_excluded"] = local
        (OUT / "requirements.txt").write_text("\n".join(kept) + "\n", encoding="utf-8")
        previous = OUT / "pip_audit.json"
        if previous.exists():
            previous.replace(OUT / "pip_audit_before.json")
        done = subprocess.run(  # noqa: S603 - fixed programs
            [
                UVX,
                "--python",
                tools["python"],
                "--from",
                f"pip-audit=={tools['pip_audit']}",
                "pip-audit",
                "-r",
                str(OUT / "requirements.txt"),
                "--no-deps",
                "--disable-pip",
                "--format",
                "json",
                "--progress-spinner",
                "off",
                "-o",
                str(OUT / "pip_audit.json"),
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode not in (0, 1) or not (OUT / "pip_audit.json").exists():
            raise SetupError(f"pip-audit failed: {done.stderr.strip()[-300:]}")
        scan = json.loads((OUT / "pip_audit.json").read_text(encoding="utf-8"))
        ids = sorted({v["id"] for d in scan["dependencies"] for v in d.get("vulns", [])})
        retrieved = datetime.now().date().isoformat()
        severities = {}
        for advisory in ids:
            try:
                severities[advisory] = {
                    "severity": level(osv(advisory)),
                    "source": OSV + advisory,
                    "retrieved": retrieved,
                }
            except OSError as error:
                raise SetupError(f"the OSV lookup of {advisory} failed ({error})") from None
        (OUT / "osv_severity.json").write_text(json.dumps(severities, indent=1) + "\n")
        policy = subprocess.run(  # noqa: S603 - fixed programs
            [
                sys.executable,
                str(REPO / "scripts" / "check_audit_policy.py"),
                "--audit",
                str(OUT / "pip_audit.json"),
                "--before",
                str(OUT / "pip_audit_before.json"),
                "--severity",
                str(OUT / "osv_severity.json"),
                "--exceptions",
                str(REPO / "config" / "security_exceptions.toml"),
                "--out",
                str(OUT / "dependency_status.json"),
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        print(policy.stdout, end="")
        if policy.returncode == 2:
            raise SetupError("the dependency policy check failed")
        secrets = subprocess.run(  # noqa: S603 - fixed programs
            [str(REPO / "scripts" / "secret_scan.sh")],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        )
        print(secrets.stdout.strip().splitlines()[-1] if secrets.stdout.strip() else "")
        if secrets.returncode == 2:
            raise SetupError("the secret scan failed")
        record["results"] = {
            "dependencies_open": policy.returncode == 1,
            "secrets_unapproved": secrets.returncode == 1,
            "advisories": len(ids),
        }
        status = 1 if policy.returncode == 1 or secrets.returncode == 1 else 0
    except (SetupError, ValueError, KeyError) as error:
        record["error"] = str(error)
        status = 2
    record["exit"] = status
    (OUT / "run.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(
        {
            0: "Audit scans: nothing open.",
            1: "Audit scans: findings are open.",
            2: f"Audit scans failed: {record.get('error')}",
        }[status]
    )
    return status


if __name__ == "__main__":
    sys.exit(main())
