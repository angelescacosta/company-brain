"""T3.AC1: every model is present with a matching digest or revision."""

import hashlib
import json
import tomllib
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
MODELS = tomllib.loads((REPO / "config" / "models.toml").read_text())
OLLAMA = "http://127.0.0.1:11434"


def tree_sha256(folder: Path) -> str:
    """Hash of every file's relative path and content, in sorted order."""
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        digest.update(path.relative_to(folder).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
    return digest.hexdigest()


def test_ollama_models_match_digests() -> None:
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=10) as response:  # noqa: S310
        installed = {m["name"]: m["digest"] for m in json.load(response)["models"]}
    for name, model in MODELS["ollama"].items():
        assert installed.get(name) == model["digest"], name


@pytest.mark.parametrize("repo", sorted(MODELS["huggingface"]))
def test_huggingface_snapshots_at_pinned_revision(repo: str) -> None:
    model = MODELS["huggingface"][repo]
    cache = Path("~/.cache/huggingface/hub").expanduser()
    snapshot = cache / f"models--{repo.replace('/', '--')}" / "snapshots" / model["revision"]
    assert (snapshot / "config.json").exists(), repo
    refs_main = snapshot.parents[1] / "refs" / "main"
    assert refs_main.read_text().strip() == model["revision"], f"{repo}: main is not pinned"
    assert tree_sha256(snapshot) == model["tree_sha256"], repo


@pytest.mark.parametrize("name", sorted(MODELS["docling"]))
def test_docling_assets(name: str) -> None:
    folder = Path(MODELS["docling"][name]["path"]).expanduser()
    assert tree_sha256(folder) == MODELS["docling"][name]["tree_sha256"], name


def test_tiktoken_files() -> None:
    cache = Path(MODELS["tiktoken"]["cache_dir"]).expanduser()
    for name, entry in MODELS["tiktoken"]["files"].items():
        data = (cache / entry["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], name


def test_embedding_prefixes_recorded_from_model_cards() -> None:
    for name, entry in MODELS["embedding"].items():
        assert name in MODELS["ollama"], name
        assert {"query_prefix", "document_prefix", "source", "licence"} <= set(entry), name
        assert entry["source"].startswith("https://huggingface.co/"), name
