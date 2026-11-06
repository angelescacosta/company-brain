"""T46.AC4: the secret scan covers the working tree and every commit on every ref.

Temporary repositories hold a planted secret in the working tree, one only in an old commit,
one only on a non-default branch, and one approved fake value in the baseline.
"""

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "secret_scan.sh"
# A private key header is found by detect-secrets' PrivateKeyDetector (planted, fake).
SECRET = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEfakefakefakefakefake\n-----END RSA PRIVATE KEY-----\n"  # noqa: S105 - a planted fake
)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def new_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("clean\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "clean")
    return repo


def scan(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), str(repo)], capture_output=True, text=True, check=False, timeout=600
    )


def test_clean_repository_passes(tmp_path: Path) -> None:
    assert scan(new_repo(tmp_path)).returncode == 0


def test_secret_in_the_working_tree_fails(tmp_path: Path) -> None:
    repo = new_repo(tmp_path)
    (repo / "key.pem").write_text(SECRET)
    assert scan(repo).returncode == 1


def test_secret_only_in_an_old_commit_fails(tmp_path: Path) -> None:
    repo = new_repo(tmp_path)
    (repo / "key.pem").write_text(SECRET)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "oops")
    (repo / "key.pem").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "removed")
    assert scan(repo).returncode == 1


def test_secret_only_on_another_branch_fails(tmp_path: Path) -> None:
    repo = new_repo(tmp_path)
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "key.pem").write_text(SECRET)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "side")
    git(repo, "checkout", "-q", "main")
    assert scan(repo).returncode == 1


def test_an_approved_fake_value_passes(tmp_path: Path) -> None:
    repo = new_repo(tmp_path)
    (repo / "fixture.pem").write_text(SECRET)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture")
    found = scan(repo)
    assert found.returncode == 1
    report = json.loads((repo / ".secret_scan_findings.json").read_text())
    baseline = {
        "results": {
            f["filename"]: [{**f, "is_secret": False, "reason": "fake key for a test"}]
            for f in report["findings"]
        }
    }
    (repo / ".secrets.baseline").write_text(json.dumps(baseline))
    assert scan(repo).returncode == 0


@pytest.mark.parametrize("field", ["reason", "is_secret"])
def test_a_baseline_entry_without_review_does_not_count(tmp_path: Path, field: str) -> None:
    repo = new_repo(tmp_path)
    (repo / "fixture.pem").write_text(SECRET)
    found = scan(repo)
    report = json.loads((repo / ".secret_scan_findings.json").read_text())
    entry = {**report["findings"][0], "is_secret": False, "reason": "fake"}
    del entry[field]
    (repo / ".secrets.baseline").write_text(json.dumps({"results": {"fixture.pem": [entry]}}))
    assert found.returncode == 1 and scan(repo).returncode == 1


def test_only_the_audit_outputs_are_skipped(tmp_path: Path) -> None:
    """The audit's own output folder is skipped; the same key anywhere else still fails."""
    repo = new_repo(tmp_path)
    (repo / "reports" / "security").mkdir(parents=True)
    (repo / "reports" / "security" / "run.json").write_text(SECRET)
    assert scan(repo).returncode == 0
    (repo / "reports" / "other.json").write_text(SECRET)
    assert scan(repo).returncode == 1


def test_hostile_path_names_never_write_outside_the_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security scan F5: names from `git ls-tree` were joined onto the temporary folder, so a
    crafted repository with `..` entries could overwrite files elsewhere."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "secret_scan", REPO / "scripts" / "secret_scan.py"
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    outside = tmp_path / "escape.txt"
    answers = {
        "ls-files": b"",
        "rev-list": b"c0ffee\n",
        "ls-tree": b"100644 blob ab12\t../../../escape.txt\x00100644 blob cd34\t/etc/abs.txt\x00",
        "cat-file": b"payload",
    }
    monkeypatch.setattr(module, "git", lambda _repo, command, *_a: answers[command])
    into = tmp_path / "a" / "b" / "copy"
    into.mkdir(parents=True)
    origin = module.collect(tmp_path, into)
    assert not outside.exists()
    copies = [p for p in into.rglob("*") if p.is_file()]
    assert len(copies) == 2 and all(p.resolve().is_relative_to(into.resolve()) for p in copies)
    assert sorted(origin.values()) == [
        "../../../escape.txt (commit c0ffee)",
        "/etc/abs.txt (commit c0ffee)",
    ]
