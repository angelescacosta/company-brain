"""Write the fictional test corpus into the Drive root and record it (T11).

Writes `Test data (fictional)/Clients/<client>/...` inside the root recorded in
`config/sources.toml`, then the T11 entries of `data/manifests/fictional.json`
and `data/facts.json`. Safe to run again: the output is reproducible, and it
refuses to overwrite a file T11 has not recorded, without opening it.
"""

import json
import sys
import tempfile
from pathlib import Path

from company_brain import corpus
from company_brain.drive import load_sources

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    root = load_sources(REPO / "config" / "sources.toml")["root"]
    target = root / corpus.PREFIX
    if not target.is_dir():
        print(f"Missing folder {target}; run scripts/setup_drive_tree.py first.")
        return 1
    manifest = REPO / "data" / "manifests" / "fictional.json"
    recorded = {}
    if manifest.exists():
        recorded = {
            e["path"]: e["sha256"]
            for e in json.loads(manifest.read_text(encoding="utf-8"))["files"]
            if e.get("task") == corpus.TASK
        }
    with tempfile.TemporaryDirectory() as scratch:
        built = corpus.build(Path(scratch))
        for entry in built.files:
            relative = entry["path"][len(corpus.PREFIX) + 1 :]
            existing = target / relative
            if existing.exists() and entry["path"] not in recorded:
                # Never open a file T11 did not record; refuse by name only.
                print(f"Refusing to overwrite {relative}: it is not a recorded T11 file.")
                return 1
        for entry in built.files:
            relative = entry["path"][len(corpus.PREFIX) + 1 :]
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((Path(scratch) / relative).read_bytes())
    corpus.write_records(REPO, built)
    print(f"{len(built.files)} files and {len(built.facts)} facts written to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
