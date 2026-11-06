"""Render the receipt and whiteboard images into the Drive root and record them (T11R).

Writes `Test data (fictional)/Clients/<client>/Receipts/...` and the T11R entries
of the fictional manifest and `data/facts.json`. Safe to run again; refuses to
overwrite a file T11R has not recorded, without opening it.
"""

import json
import sys
import tempfile
from pathlib import Path

from company_brain import corpus, image_fixtures
from company_brain.drive import load_sources

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    target = load_sources(REPO / "config" / "sources.toml")["root"] / corpus.PREFIX
    if not target.is_dir():
        print(f"Missing folder {target}; run scripts/setup_drive_tree.py first.")
        return 1
    manifest = REPO / "data" / "manifests" / "fictional.json"
    recorded = {
        e["path"]
        for e in json.loads(manifest.read_text(encoding="utf-8"))["files"]
        if e.get("task") == image_fixtures.TASK
    }
    with tempfile.TemporaryDirectory() as scratch:
        built = image_fixtures.build(Path(scratch))
        relatives = [e["path"][len(corpus.PREFIX) + 1 :] for e in built.files]
        for entry, relative in zip(built.files, relatives, strict=True):
            if (target / relative).exists() and entry["path"] not in recorded:
                print(f"Refusing to overwrite {relative}: it is not a recorded T11R file.")
                return 1
        for relative in relatives:
            (target / relative).parent.mkdir(parents=True, exist_ok=True)
            (target / relative).write_bytes((Path(scratch) / relative).read_bytes())
    image_fixtures.write_records(REPO, built)
    print(f"{len(built.files)} images and {len(built.facts)} facts written to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
