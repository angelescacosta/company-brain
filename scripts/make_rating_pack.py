"""Build the blind rating pack (T36) from a tuning run's answers.

Usage: uv run python scripts/make_rating_pack.py RUN_ID

Writes reports/rating/rate.html (the page the owner scores in, H6) and
reports/rating/key.json (the pack ID, which model gave each answer, and each
question's language). The owner opens the page with the "Rate answers" launcher.
"""

import hashlib
import json
import sys
from pathlib import Path

from company_brain import answer, rating

REPO = Path(__file__).resolve().parents[1]
SEED = 20261025


def main(run_id: str) -> int:
    records = {
        r["id"]: r
        for r in map(json.loads, (REPO / "reports" / "runs" / run_id / "questions.jsonl").open())
    }
    questions = json.loads((REPO / "eval" / "tuning.json").read_text())["questions"]
    chosen = rating.select(questions)
    items = []
    for qid in chosen:
        record = records[qid]
        sources = "\n".join(
            f"[{s.get('number', n)}] {s.get('label') or s.get('file', '')}"
            for n, s in enumerate(record.get("sources") or [], start=1)
            if isinstance(s, dict)
        )
        shown = record["answer"] + (f"\n\nSources:\n{sources}" if sources else "")
        items.append((qid, record["question"], {answer.ANSWER_MODEL: shown}))
    sheet, key = rating.pack(items, SEED)
    pack_id = hashlib.sha256(json.dumps(sheet, sort_keys=True).encode()).hexdigest()[:16]
    out = REPO / "reports" / "rating"
    out.mkdir(parents=True, exist_ok=True)
    (out / "rate.html").write_text(rating.page(sheet, pack_id), encoding="utf-8")
    (out / "key.json").write_text(
        json.dumps(
            {
                "pack": pack_id,
                "run_id": run_id,
                "seed": SEED,
                "rows": [[qid, letter, model] for (qid, letter), model in sorted(key.items())],
                "languages": {qid: records[qid]["language"] for qid in chosen},
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Rating pack {pack_id}: {len(sheet)} questions, {len(key)} answers to score.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
