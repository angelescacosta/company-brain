"""T5.AC3: no file in the repository contains a password."""

import re
import subprocess
from pathlib import Path

from company_brain.keychain import get_password
from company_brain.settings import Settings

REPO = Path(__file__).resolve().parents[1]
ASSIGNMENT = re.compile(
    r"""(?ix)(password|passwd|pwd|secret)\s*[:=]\s*["'][^"'\s{}%]{6,}["']"""
    r"""|postgres(?:ql)?://[^:/\s]+:[^@/\s]+@"""
)


def repository_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    return [REPO / name.decode() for name in listed if name and (REPO / name.decode()).is_file()]


def test_no_password_assignments() -> None:
    hits = []
    for path in repository_files():
        if path.suffix in {".jsonl", ".stderr"}:
            continue  # review transcripts; covered by the T46 secret scan
        text = path.read_text(errors="ignore")
        hits += [f"{path.relative_to(REPO)}: {m.group(0)[:20]}" for m in ASSIGNMENT.finditer(text)]
    assert hits == []


def test_test_role_password_not_in_repository() -> None:
    password = get_password(f"{Settings.from_env().keychain_prefix}-db", "brain_test")
    assert len(password) >= 24
    for path in repository_files():
        assert password.encode() not in path.read_bytes(), path
