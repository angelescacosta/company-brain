"""Seal the locked questions after the owner's review (T14S).

Usage: uv run python scripts/seal_questions.py

Encrypts `eval/review/locked.json` to `eval/locked.json.age` (key in the Keychain
only), checks it opens to the same questions, records its hash, deletes
`eval/review/`, the given temporary copies and the review sheet's local synced
copy, then searches the project, data and temporary folders for any plain copy.
"""

import json
import shutil
import sys
from pathlib import Path

from company_brain import seal
from company_brain.drive import load_sources
from company_brain.settings import Settings

REPO = Path(__file__).resolve().parents[1]
SHEET = "Company Brain - test questions to check.xlsx"


def main(extra: list[str]) -> int:
    review = REPO / "eval" / "review"
    plain = review / "locked.json"
    out = REPO / "eval" / "locked.json.age"
    texts = [q["question"] for q in json.loads(plain.read_text(encoding="utf-8"))["questions"]]
    digest = seal.seal(plain, out)
    (REPO / "eval" / "locked.json.age.sha256").write_text(f"{digest}  locked.json.age\n")
    shutil.rmtree(review)
    sheet = load_sources(REPO / "config" / "sources.toml")["root"].parent / SHEET
    if sheet.exists():
        sheet.unlink()  # Drive for Desktop moves it to Drive's trash
    for path in map(Path, extra):
        if path.exists():
            path.unlink()
    settings = Settings.from_env()
    folders = [REPO / p for p in ("eval", "reports", "data", "src", "tests", "scripts", "docs")]
    folders += [settings.data_dir, settings.exports_dir, *map(Path, sys.argv[2:3])]
    left = seal.plain_copies(texts, [f for f in folders if f.exists()])
    print(f"sealed: eval/locked.json.age sha256 {digest}")
    print(f"review sheet local copy present: {sheet.exists()}")
    print("plain copies left:", [str(p) for p in left] or "none")
    return 0 if not left and not sheet.exists() else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:2]))
