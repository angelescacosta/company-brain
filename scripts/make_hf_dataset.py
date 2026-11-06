"""Build the Hugging Face dataset folder (KeepCoding deliverable).

Copies the fictional test documents listed in the manifest (checked against
their hashes), the tuning questions as JSONL, the expected facts and the
dataset card. The methodology and the public documents stay out: the first is
the business's own, the second belong to their publishers and are linked.

Usage: uv run python scripts/make_hf_dataset.py [destination]
"""

import hashlib
import json
import shutil
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PREFIX = "Test data (fictional)/"


def main() -> None:
    dest = (
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path.home() / "Documents" / "CompanyBrain-dataset"
    )
    root = Path(tomllib.loads((REPO / "config" / "sources.toml").read_text())["root"]["path"])
    manifest = json.loads((REPO / "data" / "manifests" / "fictional.json").read_text())["files"]

    for child in dest.glob("*"):
        if child.name != ".git":
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    dest.mkdir(parents=True, exist_ok=True)

    copied = 0
    for entry in manifest:
        if not entry["path"].startswith(PREFIX):
            continue
        source = root / entry["path"]
        if hashlib.sha256(source.read_bytes()).hexdigest() != entry["sha256"]:
            sys.exit(f"Hash mismatch, not copied: {entry['path']}")
        target = dest / "documents" / entry["path"].removeprefix(PREFIX)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1

    questions = json.loads((REPO / "eval" / "tuning.json").read_text())["questions"]
    missing = set()
    (dest / "questions").mkdir()
    with (dest / "questions" / "tuning.jsonl").open("w") as out:
        for question in questions:
            for source in question.get("required_sources", []):
                if source["file"].startswith(PREFIX):
                    source["file"] = "documents/" + source["file"].removeprefix(PREFIX)
                else:
                    missing.add(source["file"])
            out.write(json.dumps(question, ensure_ascii=False) + "\n")

    shutil.copy2(REPO / "data" / "facts.json", dest / "facts.json")
    shutil.copy2(REPO / "publish" / "huggingface" / "README.md", dest / "README.md")

    print(f"{copied} documents and {len(questions)} questions in {dest}")
    if missing:
        print("Sources not in the dataset (methodology or public):", *sorted(missing), sep="\n  ")


if __name__ == "__main__":
    main()
