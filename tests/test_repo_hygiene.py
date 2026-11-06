"""T1.AC2: git ignores data, models, backups, exports, secrets, dumps and archives."""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

IGNORED = [
    "data/system.db",
    "data/markdown/doc.md",
    "CompanyBrainData/x",
    "CompanyBrainExports/x.pdf",
    "models/qwen.gguf",
    "weights.safetensors",
    "backups/archive.age",
    "Company Brain Backups/2026-11-01.tar.age",
    "exports/proposal.docx",
    ".env",
    ".env.local",
    "company_brain.dump",
    "pg.sql.gz",
    "openwebui.sqlite",
    "archive.tar.gz",
    "archive.zip",
    "eval/locked.json.age",
    "eval/review/locked_questions.json",
    "reports/runs/r1/questions.jsonl",
    "code.bundle",
    ".webui_secret_key",
]

TRACKED = [
    "data/manifests/fictional.json",
    "data/facts.json",
    "src/company_brain/isolation.py",
    "config/real_resource_allowlist.toml",
    "PRD.md",
]


def _ignored(path: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-q", path], cwd=REPO, check=False
    )
    return result.returncode == 0


@pytest.mark.parametrize("path", IGNORED)
def test_sensitive_paths_are_ignored(path: str) -> None:
    assert _ignored(path), path


@pytest.mark.parametrize("path", TRACKED)
def test_project_files_are_not_ignored(path: str) -> None:
    assert not _ignored(path), path
