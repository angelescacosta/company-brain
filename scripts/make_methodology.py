"""Write the methodology draft into the Drive root's Methodology folder and record it (T13).

Safe to run again; refuses to overwrite a file T13 has not recorded, without opening it.
"""

import json
import sys
import tempfile
from pathlib import Path

from company_brain import methodology
from company_brain.corpus import merge_records
from company_brain.drive import load_sources

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    target = load_sources(REPO / "config" / "sources.toml")["root"] / methodology.PREFIX
    if not target.is_dir():
        print(f"Missing folder {target}; run scripts/setup_drive_tree.py first.")
        return 1
    manifest = REPO / "data" / "manifests" / "fictional.json"
    recorded = {
        e["path"]
        for e in json.loads(manifest.read_text(encoding="utf-8"))["files"]
        if e.get("task") == methodology.TASK
    }
    with tempfile.TemporaryDirectory() as scratch:
        entries = methodology.build(Path(scratch))
        for entry in entries:
            name = Path(entry["path"]).name
            if (target / name).exists() and entry["path"] not in recorded:
                print(f"Refusing to overwrite {name}: it is not a recorded T13 file.")
                return 1
        for entry in entries:
            name = Path(entry["path"]).name
            (target / name).write_bytes((Path(scratch) / name).read_bytes())
    merge_records(manifest, "files", methodology.TASK, entries)
    print(f"{len(entries)} files written to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
