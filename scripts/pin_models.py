"""Record the downloaded models' digests and revisions in config/models.toml (T3).

Usage: uv run python scripts/pin_models.py
Run once after the downloads; tests/integration/test_models_present.py then
checks that the installed models still match.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

OLLAMA_MODELS = [
    "qwen3.5:9b",
    "qwen3.5:4b",
    "gemma4:12b",
    "qwen3-embedding:0.6b",
    "bge-m3:latest",
    "embeddinggemma-2:270m",
]
HUGGINGFACE = {
    "BAAI/bge-reranker-v2-m3": ("953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e", "apache-2.0"),
    # The reranker in use since H12 (D31); bge-reranker-v2-m3 stays pinned for comparison.
    "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1": (
        "1427fd652930e4ba29e8149678df786c240d8825",
        "apache-2.0",
    ),
    "vectara/hallucination_evaluation_model": (
        "de9a5e6ba9ba49531a022dba17e35ffeb2a6fad8",
        "apache-2.0",
    ),
    "google/flan-t5-base": ("7bcac572ce56db69c1ea7c8af255c5d7c9672fc2", "apache-2.0"),
}
DOCLING_DIR = Path("~/.cache/docling/models")
TIKTOKEN = {
    "o200k_base": "fb374d419588a4632f3f557e76b4b70aebbca790",
    "cl100k_base": "9b5ad71b2ce5302211f9c61530b329a4922fc6a4",
}

# Query and document prefixes, taken from each official model card.
EMBEDDING = {
    "qwen3-embedding:0.6b": {
        "query_prefix": (
            "Instruct: Given a web search query, retrieve relevant passages that answer "
            "the query\nQuery:"
        ),
        "document_prefix": "",
        "source": "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/blob/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3/README.md",
        "licence": "apache-2.0",
    },
    "bge-m3:latest": {
        "query_prefix": "",
        "document_prefix": "",
        "source": "https://huggingface.co/BAAI/bge-m3/blob/5617a9f61b028005a4858fdac845db406aefb181/README.md",
        "licence": "mit",
    },
    "embeddinggemma-2:270m": {
        "query_prefix": "task: search result | query: ",
        "document_prefix": "title: none | text: ",
        "source": "https://huggingface.co/google/embeddinggemma-2/blob/914f7f89142e33e77833254d9c9b90c3cef7303b/README.md",
        "licence": "apache-2.0",
    },
}


def tree_sha256(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        digest.update(path.relative_to(folder).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode() + b"\n")
    return digest.hexdigest()


def toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def main() -> None:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as response:  # noqa: S310
        tags = {m["name"]: m for m in json.load(response)["models"]}
    lines = [
        "# Pinned models and conversion assets (T3). Written by scripts/pin_models.py;",
        "# checked by tests/integration/test_models_present.py.",
        "",
    ]
    for name in OLLAMA_MODELS:
        model = tags[name]
        lines += [
            f"[ollama.{toml_string(name)}]",
            f"digest = {toml_string(model['digest'])}",
            f"size = {model['size']}",
            f"parameters = {toml_string(model['details'].get('parameter_size', ''))}",
            f"quantization = {toml_string(model['details'].get('quantization_level', ''))}",
            "",
        ]
    for name, entry in EMBEDDING.items():
        lines.append(f"[embedding.{toml_string(name)}]")
        lines += [f"{key} = {toml_string(value)}" for key, value in entry.items()]
        lines.append("")
    cache = Path("~/.cache/huggingface/hub").expanduser()
    for repo, (revision, licence) in HUGGINGFACE.items():
        folder = cache / f"models--{repo.replace('/', '--')}"
        snapshot = folder / "snapshots" / revision
        # HHEM loads flan-t5-base by the name "main"; point "main" at the pinned revision.
        (folder / "refs").mkdir(exist_ok=True)
        (folder / "refs" / "main").write_text(revision)
        lines += [
            f"[huggingface.{toml_string(repo)}]",
            f"revision = {toml_string(revision)}",
            f"licence = {toml_string(licence)}",
            f"tree_sha256 = {toml_string(tree_sha256(snapshot))}",
            "",
        ]
    docling = DOCLING_DIR.expanduser()
    for folder in sorted(p for p in docling.iterdir() if p.is_dir()):
        lines += [
            f"[docling.{toml_string(folder.name)}]",
            f"path = {toml_string(str(DOCLING_DIR / folder.name))}",
            f"tree_sha256 = {toml_string(tree_sha256(folder))}",
            "",
        ]
    lines += ["[tiktoken]", 'cache_dir = "~/.cache/tiktoken"', ""]
    tiktoken_dir = Path("~/.cache/tiktoken").expanduser()
    for name, file in TIKTOKEN.items():
        sha = hashlib.sha256((tiktoken_dir / file).read_bytes()).hexdigest()
        lines += [f"[tiktoken.files.{name}]", f'file = "{file}"', f'sha256 = "{sha}"', ""]
    Path("config/models.toml").write_text("\n".join(lines))
    print("wrote config/models.toml")


if __name__ == "__main__":
    main()
