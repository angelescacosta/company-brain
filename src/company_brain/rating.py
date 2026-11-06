"""The blind rating pack (T36) for the owner's scores (H6).

Twenty tuning questions, a fixed stratified choice: per language, categories in name order
take turns giving their next question by ID until there are ten, and at least two are
unanswerable. Each row shows the answers under letters in a random order per row (a
seeded shuffle); the key maps each (question, letter) to its model, which never appears
on the sheet. Unblinding refuses while any score is empty or outside 1 to 5.
"""

from __future__ import annotations

import csv
import hashlib
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PER_LANGUAGE = 10
MIN_UNANSWERABLE = 2
LETTERS = "ABCDEFGH"


class IncompleteRatings(ValueError):
    """Some score is missing or not a whole number from 1 to 5."""


def select(questions: Sequence[dict[str, Any]]) -> list[str]:
    chosen: list[str] = []
    for language in ("en", "es"):
        groups: dict[str, list[str]] = {}
        for q in sorted(questions, key=lambda q: q["id"]):
            if q["language"] == language:
                groups.setdefault(q["category"], []).append(q["id"])
        picked: list[str] = groups.get("unanswerable", [])[:MIN_UNANSWERABLE]
        while len(picked) < PER_LANGUAGE and any(
            i not in picked for ids in groups.values() for i in ids
        ):
            for category in sorted(groups):
                left = [i for i in groups[category] if i not in picked]
                if left and len(picked) < PER_LANGUAGE:
                    picked.append(left[0])
        chosen += sorted(picked)
    return chosen


Item = tuple[str, str, Mapping[str, str]]  # question ID, question, model -> answer


def pack(
    items: Sequence[Item], seed: int
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], str]]:
    rng = random.Random(seed)  # noqa: S311 - an order to hide names from the rater, not a secret
    sheet: list[dict[str, Any]] = []
    key: dict[tuple[str, str], str] = {}
    for qid, question, per_model in items:
        models = sorted(per_model)
        rng.shuffle(models)
        shown = {}
        for letter, model in zip(LETTERS, models, strict=False):
            shown[letter] = per_model[model]
            key[(qid, letter)] = model
        sheet.append({"id": qid, "question": question, "answers": shown})
    return sheet, key


def unblind(
    scores: Mapping[tuple[str, str], int | None],
    key: Mapping[tuple[str, str], str],
    languages: Mapping[str, str],
    out: Path,
) -> None:
    """Write `reports/blind_ratings_unblinded.csv` and its SHA-256, only when every score is in."""
    missing = [
        f"{qid} {letter}"
        for qid, letter in key
        if not isinstance(scores.get((qid, letter)), int)
        or not 1 <= int(scores[(qid, letter)] or 0) <= 5
    ]
    if missing:
        raise IncompleteRatings("every answer needs a score from 1 to 5: " + ", ".join(missing))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "language", "model", "score"])
        for (qid, letter), model in sorted(key.items()):
            writer.writerow([qid, languages[qid], model, scores[(qid, letter)]])
    Path(f"{out}.sha256").write_text(hashlib.sha256(out.read_bytes()).hexdigest() + "\n")
    from company_brain import dashboard

    dashboard.rebuild_after("unblinding")  # T34D: sections 2 and 3 appear after this


# The rating page (the owner chose a page over a spreadsheet, 25 Oct; D41) ---------------

RUBRIC = {
    5: "Ready to send: correct, complete, in the right language and tone.",
    4: "Small edits: correct, with a minor gap or awkward wording.",
    3: "Usable with work: mostly right, but something is missing or unclear.",
    2: "Mostly wrong or unhelpful.",
    1: "Wrong, misleading or unusable.",
}
NOTE = (
    "For a question the documents do not answer, a clear 'I could not find this' is the right "
    "answer. Judge accuracy, completeness, tone and language; the [1] numbers are sources."
)
POLICY = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"
_SCRIPT = """
const radios = [...document.querySelectorAll('input[type=radio]')];
const names = [...new Set(radios.map(r => r.name))];
const save = document.getElementById('save');
function scores() {
  const out = {};
  for (const name of names) {
    const picked = document.querySelector('input[name="' + CSS.escape(name) + '"]:checked');
    if (picked) out[name] = Number(picked.value);
  }
  return out;
}
function update() {
  const done = Object.keys(scores()).length;
  save.disabled = done < names.length;
  document.getElementById('count').textContent = done + ' of ' + names.length + ' scored';
}
radios.forEach(r => r.addEventListener('change', update));
save.addEventListener('click', () => {
  if (Object.keys(scores()).length < names.length) return;
  const body = JSON.stringify({pack: document.body.dataset.pack, scores: scores()});
  const link = document.createElement('a');
  link.href = 'data:application/json;charset=utf-8,' + encodeURIComponent(body);
  link.download = 'company-brain-scores.json';
  link.click();
});
update();
"""


def page(sheet: Sequence[dict[str, Any]], pack_id: str) -> str:
    """One self-contained page: no links, no external files, nothing sent anywhere."""
    from html import escape

    rubric = "".join(
        f"<li><b>{n}</b> {escape(RUBRIC[n])}</li>" for n in sorted(RUBRIC, reverse=True)
    )
    rows = []
    for number, row in enumerate(sheet, start=1):
        answers = []
        for letter, text in row["answers"].items():
            name = escape(f"{row['id']}|{letter}")
            buttons = "".join(
                f'<label><input type="radio" name="{name}" value="{n}"> {n}</label>'
                for n in range(1, 6)
            )
            answers.append(
                f"<h3>Answer {letter}</h3><pre>{escape(text)}</pre>"
                f'<p class="score">Score: {buttons}</p>'
            )
        rows.append(
            f"<section><h2>Question {number} of {len(sheet)}</h2>"
            f'<p class="q">{escape(row["question"])}</p>{"".join(answers)}</section>'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Rate answers</title><style>"
        "body{font:16px/1.5 -apple-system,sans-serif;max-width:820px;margin:0 auto;padding:16px;"
        "background:#fff;color:#222}section{border-top:1px solid #ccc;padding:8px 0}"
        "pre{white-space:pre-wrap;font:inherit;background:#f6f6f6;padding:8px}"
        ".q{font-weight:600}.score label{margin-right:14px}#bar{position:sticky;bottom:0;"
        "background:#fff;padding:12px 0;border-top:1px solid #ccc}button{font-size:16px;"
        "padding:8px 16px}"
        f'</style></head><body data-pack="{escape(pack_id)}">'
        "<h1>Rate the assistant's answers</h1>"
        f'<ol class="rubric">{rubric}</ol><p>{escape(NOTE)}</p>{"".join(rows)}'
        '<div id="bar"><button id="save" disabled>Save my scores</button> '
        '<span id="count"></span></div>'
        f"<script>{_SCRIPT}</script></body></html>\n"
    )


def read_scores(saved: dict[str, Any], pack_id: str) -> dict[tuple[str, str], int | None]:
    """The scores from the saved file, if it belongs to this pack."""
    if saved.get("pack") != pack_id:
        raise IncompleteRatings("these scores belong to another rating pack")
    out: dict[tuple[str, str], int | None] = {}
    for name, value in dict(saved.get("scores", {})).items():
        qid, _, letter = str(name).rpartition("|")
        out[(qid, letter)] = value if isinstance(value, int) else None
    return out


def latest_scores(downloads: Path) -> Path | None:
    """The newest scores file the page saved (a browser may add ' 2' or '(1)' to the name)."""
    found = sorted(downloads.glob("company-brain-scores*.json"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None
