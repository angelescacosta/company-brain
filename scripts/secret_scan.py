"""T46.AC4: scan the working tree and every commit reachable from any ref for secrets.

Every file in the working tree (tracked and untracked, not ignored) and every file of
every commit from `git rev-list --all` is copied into a temporary folder and scanned
with detect-secrets (offline, no verification probes). A finding passes only if
`.secrets.baseline` holds the same hashed secret marked `"is_secret": false` with a
`"reason"`: an individually reviewed fake value. Approving an entry is a separate step.
Writes `.secret_scan_findings.json` in the scanned repository.
Exit codes: 0 nothing unapproved, 1 unapproved findings, 2 the scan itself failed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SKIP = {".secrets.baseline", ".secret_scan_findings.json"}
# The audit's own machine outputs (git commit and lock hashes, public advisory data),
# rewritten by every run; reviewed once (T46) and skipped by path, never by content.
OWN_OUTPUTS = "reports/security/"


def skipped(name: str) -> bool:
    return Path(name).name in SKIP or name.startswith(OWN_OUTPUTS)


UVX = str(Path.home() / ".local" / "bin" / "uvx")


def git(repo: Path, *args: str) -> bytes:
    done = subprocess.run(  # noqa: S603 - a fixed program
        ["/usr/bin/git", "-C", str(repo), *args], capture_output=True, check=True
    )
    return done.stdout


def _copy_name(folder: str, number: int, name: str) -> str:
    """A name made here, never from git: a crafted path such as `../../x` or `/etc/x` cannot
    leave the copy (security scan F5). The extension is kept for the scanner's file checks."""
    suffix = "".join(c for c in Path(name).suffix if c.isalnum() or c == ".")[:12]
    return f"{folder}/{number:06d}{suffix}"


def collect(repo: Path, into: Path) -> dict[str, str]:
    """Copy every file to scan into `into`; return copy name -> where it came from."""
    origin: dict[str, str] = {}
    for folder in ("tree", "history"):
        (into / folder).mkdir(parents=True, exist_ok=True)
    listed = git(repo, "ls-files", "-co", "--exclude-standard", "-z").decode().split("\0")
    for name in filter(None, listed):
        source = repo / name
        if skipped(name) or source.is_symlink() or not source.is_file():
            continue
        copy = _copy_name("tree", len(origin), name)
        (into / copy).write_bytes(source.read_bytes())
        origin[copy] = f"{name} (working tree)"
    seen: set[tuple[str, str]] = set()
    for commit in git(repo, "rev-list", "--all").decode().split():
        for entry in git(repo, "ls-tree", "-r", "-z", commit).decode().split("\0"):
            if not entry:
                continue
            meta, name = entry.split("\t", 1)
            kind, blob = meta.split()[1:3]
            if kind != "blob" or skipped(name) or (blob, name) in seen:
                continue
            seen.add((blob, name))
            copy = _copy_name("history", len(origin), name)
            (into / copy).write_bytes(git(repo, "cat-file", "blob", blob))
            origin[copy] = f"{name} (commit {commit[:10]})"
    return origin


def approved(repo: Path) -> set[str]:
    path = repo / ".secrets.baseline"
    if not path.exists():
        return set()
    results = json.loads(path.read_text(encoding="utf-8")).get("results", {})
    return {
        entry["hashed_secret"]
        for entries in results.values()
        for entry in entries
        if entry.get("is_secret") is False
        and str(entry.get("reason", "")).strip()
        and entry.get("hashed_secret")
    }


def main(argv: list[str]) -> int:
    repo = Path(argv[0] if argv else HERE).resolve()
    tools = tomllib.loads((HERE / "config" / "dev_tools.toml").read_text(encoding="utf-8"))
    try:
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            origin = collect(repo, work)
            done = subprocess.run(  # noqa: S603 - fixed programs
                [
                    UVX,
                    "--python",
                    tools["python"],
                    "--from",
                    f"detect-secrets=={tools['detect_secrets']}",
                    "detect-secrets",
                    "scan",
                    "--all-files",
                    ".",
                ],
                cwd=work,
                capture_output=True,
                text=True,
                check=True,
            )
            results = json.loads(done.stdout)["results"]
    except (subprocess.CalledProcessError, ValueError, KeyError, OSError) as error:
        print(f"The secret scan failed: {type(error).__name__}")
        return 2
    ok = approved(repo)
    findings = [
        {
            "filename": origin.get(name, name).split(" (")[0],
            "where": origin.get(name, name),
            "type": f["type"],
            "hashed_secret": f["hashed_secret"],
            "line_number": f["line_number"],
        }
        for name, found in sorted(results.items())
        for f in found
    ]
    unapproved = [f for f in findings if f["hashed_secret"] not in ok]
    (repo / ".secret_scan_findings.json").write_text(
        json.dumps({"findings": findings, "unapproved": unapproved}, indent=1) + "\n"
    )
    for f in unapproved:
        print(f"Secret ({f['type']}) in {f['where']}, line {f['line_number']}")
    print(f"{len(findings)} findings, {len(unapproved)} not in the reviewed baseline.")
    return 1 if unapproved else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
