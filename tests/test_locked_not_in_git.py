"""T14.AC4: no locked question, and nothing under eval/review/, was ever committed.

Searches every commit on every branch.
"""

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCKED = REPO / "eval" / "review" / "locked.json"


def git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed program, arguments built here
        ["/usr/bin/git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def test_review_folder_never_committed() -> None:
    paths = git("log", "--all", "--name-only", "--format=")
    assert not [p for p in paths.splitlines() if p.startswith("eval/review/")]


def test_locked_question_text_never_committed() -> None:
    if not LOCKED.exists():
        return  # sealed (T14S); the folder check above still applies
    texts = [q["question"] for q in json.loads(LOCKED.read_text(encoding="utf-8"))["questions"]]
    commits = git("rev-list", "--all").split()
    patterns = [arg for text in texts for arg in ("-e", text)]
    found = subprocess.run(  # noqa: S603
        ["/usr/bin/git", "grep", "-F", "-l", *patterns, *commits],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert found.returncode == 1, found.stdout[:500]
