"""Split the reviewed question pack into tuning and locked parts (T14).

Usage: uv run python scripts/split_questions.py

Reads `eval/review/all.json` (git-ignored), writes `eval/tuning.json`
(committed) and `eval/review/locked.json` (never committed; sealed in T14S).
"""

import json
import sys
from pathlib import Path

from company_brain.questions import SEED, split, validate

REPO = Path(__file__).resolve().parents[1]


def write(path: Path, part: str, items: list[dict[str, object]]) -> None:
    pack = {"split": part, "seed": SEED, "questions": items}
    validate(pack)
    path.write_text(json.dumps(pack, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    everything = json.loads((REPO / "eval" / "review" / "all.json").read_text(encoding="utf-8"))
    validate(everything)
    tuning, locked = split(everything["questions"], SEED)
    write(REPO / "eval" / "tuning.json", "tuning", tuning)
    write(REPO / "eval" / "review" / "locked.json", "locked", locked)
    print(f"{len(tuning)} tuning and {len(locked)} locked questions written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
