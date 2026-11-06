"""T36: the blind rating pack. Twenty tuning questions, a stratified and fixed choice (10 per
language, every category, at least two unanswerable per language); answers shown as A (and B)
in a random order per row with model names hidden; a key that maps every score back; and an
unblind step that refuses while any score is empty."""

import csv
import hashlib
import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from company_brain import rating

REPO = Path(__file__).resolve().parents[1]
QUESTIONS = json.loads((REPO / "eval" / "tuning.json").read_text())["questions"]


def test_twenty_questions_stratified_and_fixed() -> None:
    chosen = rating.select(QUESTIONS)
    assert chosen == rating.select(list(reversed(QUESTIONS)))  # order of the file does not matter
    assert len(chosen) == 20 and len(set(chosen)) == 20
    by_id = {q["id"]: q for q in QUESTIONS}
    for language in ("en", "es"):
        mine = [by_id[i] for i in chosen if by_id[i]["language"] == language]
        assert len(mine) == 10
        assert {q["category"] for q in mine} == {
            q["category"] for q in QUESTIONS if q["language"] == language
        }
        assert sum(q["category"] == "unanswerable" for q in mine) >= 2


answers = st.dictionaries(
    st.sampled_from(["qwen3.5:4b", "gemma4:12b"]), st.text(min_size=1, max_size=20), min_size=1
)


@given(st.integers(min_value=0, max_value=2**32), st.lists(answers, min_size=1, max_size=6))
def test_shuffle_is_a_bijection_that_the_key_inverts(
    seed: int, table: list[dict[str, str]]
) -> None:
    """Protects the scores: for any seed, every answer appears exactly once, under a letter, and
    the key maps each (row, letter) back to its model; no model name reaches the sheet."""
    items = [(f"q{n}", f"Question {n}", per_model) for n, per_model in enumerate(table)]
    sheet, key = rating.pack(items, seed)
    shown = {(row["id"], letter) for row in sheet for letter in row["answers"]}
    assert shown == set(key)
    for (qid, letter), model in key.items():
        row = next(r for r in sheet if r["id"] == qid)
        assert row["answers"][letter] == dict(table[int(qid[1:])])[model]
    assert sorted(m for (_q, _l), m in key.items()) == sorted(
        m for per_model in table for m in per_model
    )
    assert "qwen" not in json.dumps(sheet) and "gemma" not in json.dumps(sheet)


def test_order_is_random_per_row_but_reproducible() -> None:
    items = [(f"q{n}", "Q", {"m1": "one", "m2": "two"}) for n in range(30)]
    _sheet, key = rating.pack(items, 7)
    firsts = {key[(f"q{n}", "A")] for n in range(30)}
    assert firsts == {"m1", "m2"}
    assert rating.pack(items, 7)[1] == key


def test_unblinding_refuses_any_empty_or_invalid_score(tmp_path: Path) -> None:
    key = {("q1", "A"): "qwen3.5:4b", ("q2", "A"): "qwen3.5:4b"}
    languages = {"q1": "en", "q2": "es"}
    out = tmp_path / "blind_ratings_unblinded.csv"
    for scores in (
        {("q1", "A"): 4},
        {("q1", "A"): 4, ("q2", "A"): None},
        {("q1", "A"): 4, ("q2", "A"): 6},
    ):
        with pytest.raises(rating.IncompleteRatings):
            rating.unblind(scores, key, languages, out)
        assert not out.exists()
    rating.unblind({("q1", "A"): 4, ("q2", "A"): 1}, key, languages, out)
    with out.open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows == [
        {"id": "q1", "language": "en", "model": "qwen3.5:4b", "score": "4"},
        {"id": "q2", "language": "es", "model": "qwen3.5:4b", "score": "1"},
    ]
    assert Path(f"{out}.sha256").read_text().strip() == hashlib.sha256(out.read_bytes()).hexdigest()


SHEET = [
    {"id": "q-en-1", "question": "What fee <b>?</b>", "answers": {"A": "EUR 10 [1]"}},
    {"id": "q-es-2", "question": "¿Cuándo?", "answers": {"A": "El 3 de mayo [1]"}},
]


def test_the_page_is_self_contained_and_escaped() -> None:
    page = rating.page(SHEET, "pack-1")
    assert "Content-Security-Policy" in page and "default-src 'none'" in page
    assert "<b>?</b>" not in page and "&lt;b&gt;" in page
    assert page.count('type="radio"') == 2 * 5
    assert "http://" not in page and "https://" not in page and "src=" not in page
    for score in range(1, 6):
        assert rating.RUBRIC[score] in page


def test_saving_is_refused_until_every_answer_has_a_score(tmp_path: Path) -> None:
    """T36.AC3, in a real browser: the button stays disabled while any score is empty, and the
    saved file holds the pack ID and every score."""
    from playwright.sync_api import sync_playwright

    path = tmp_path / "rate.html"
    path.write_text(rating.page(SHEET, "pack-1"), encoding="utf-8")
    requests: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        view = browser.new_page(accept_downloads=True)
        view.on("request", lambda r: requests.append(r.url))
        view.goto(path.as_uri())
        save = view.locator("#save")
        assert save.is_disabled()
        view.locator('input[name="q-en-1|A"][value="4"]').check()
        assert save.is_disabled()
        view.locator('input[name="q-es-2|A"][value="2"]').check()
        assert save.is_enabled()
        with view.expect_download() as download:
            save.click()
        saved = json.loads(Path(download.value.path()).read_text())
        browser.close()
    assert requests == [path.as_uri()]
    assert saved == {"pack": "pack-1", "scores": {"q-en-1|A": 4, "q-es-2|A": 2}}
    assert rating.read_scores(saved, "pack-1") == {("q-en-1", "A"): 4, ("q-es-2", "A"): 2}
    with pytest.raises(rating.IncompleteRatings):
        rating.read_scores({**saved, "pack": "another pack"}, "pack-1")


def test_the_newest_saved_scores_are_found(tmp_path: Path) -> None:
    """Safari names a second download 'company-brain-scores 2.json'; the newest one counts."""
    import os

    assert rating.latest_scores(tmp_path) is None
    old = tmp_path / "company-brain-scores.json"
    new = tmp_path / "company-brain-scores 2.json"
    (tmp_path / "other.json").write_text("{}")
    old.write_text("{}")
    new.write_text("{}")
    os.utime(old, (1, 1))
    assert rating.latest_scores(tmp_path) == new


def test_rate_answers_saves_only_a_complete_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launcher flow: a set with a missing score is refused, a complete one is unblinded."""
    from company_brain import launcher

    pack_dir = tmp_path / "reports" / "rating"
    pack_dir.mkdir(parents=True)
    (pack_dir / "rate.html").write_text(rating.page(SHEET, "pack-1"))
    rows = [["q-en-1", "A", "qwen3.5:4b"], ["q-es-2", "A", "qwen3.5:4b"]]
    (pack_dir / "key.json").write_text(
        json.dumps({"pack": "pack-1", "rows": rows, "languages": {"q-en-1": "en", "q-es-2": "es"}})
    )
    monkeypatch.setattr(launcher, "RATING", pack_dir)
    monkeypatch.setattr(launcher, "open_file", lambda _path: None)
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    saves = iter(
        [
            {"pack": "pack-1", "scores": {"q-en-1|A": 4}},
            {"pack": "pack-1", "scores": {"q-en-1|A": 4, "q-es-2|A": 3}},
        ]
    )

    def press_return(_prompt: str) -> str:
        (downloads / "company-brain-scores.json").write_text(json.dumps(next(saves)))
        return ""

    assert launcher.run_rating(downloads, press_return) == 0
    unblinded = tmp_path / "reports" / "blind_ratings_unblinded.csv"
    assert unblinded.read_text().splitlines()[1:] == [
        "q-en-1,en,qwen3.5:4b,4",
        "q-es-2,es,qwen3.5:4b,3",
    ]
    assert launcher.run_rating(downloads, press_return) == 0  # already saved: nothing asked
