"""T14.AC1 and AC2: the question packs validate, have the exact counts and never mix clients.

The tuning pack is committed; the locked pack lives only in `eval/review/`
(git-ignored) until T14S seals it, after which only its sealed file remains.
"""

import json
from collections import Counter
from pathlib import Path

import jsonschema
import pytest

from company_brain import questions

REPO = Path(__file__).resolve().parents[1]
TUNING = REPO / "eval" / "tuning.json"
LOCKED = REPO / "eval" / "review" / "locked.json"
SEALED = REPO / "eval" / "locked.json.age"


def pack(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def facts() -> dict[str, dict]:
    records = json.loads((REPO / "data" / "facts.json").read_text(encoding="utf-8"))["facts"]
    return {f["id"]: f for f in records}


def test_tuning_pack_is_valid() -> None:
    data = pack(TUNING)
    questions.validate(data)
    assert data["split"] == "tuning"
    assert len(data["questions"]) == 44
    assert Counter(q["language"] for q in data["questions"]) == {"en": 22, "es": 22}
    for language in ("en", "es"):
        found = {q["category"] for q in data["questions"] if q["language"] == language}
        assert found == set(questions.CATEGORIES)


@pytest.mark.skipif(not LOCKED.exists(), reason="sealed by T14S; checked by tests/test_sealing.py")
def test_locked_pack_and_totals() -> None:
    tuning, locked = pack(TUNING), pack(LOCKED)
    questions.validate(locked)
    assert locked["split"] == "locked" and len(locked["questions"]) == 22
    everything = tuning["questions"] + locked["questions"]
    assert len({q["id"] for q in everything}) == 66
    for language in ("en", "es"):
        counts = Counter(q["category"] for q in everything if q["language"] == language)
        assert counts == questions.TARGET_COUNTS
        found = {q["category"] for q in locked["questions"] if q["language"] == language}
        assert found == set(questions.CATEGORIES)
    # AC2: no shared client and no shared document.
    for field in ("client",):
        assert not {q[field] for q in tuning["questions"]} & {q[field] for q in locked["questions"]}
    files = lambda qs: {s["file"] for q in qs for s in q["required_sources"]}  # noqa: E731
    assert not files(tuning["questions"]) & files(locked["questions"])


def test_locked_pack_exists_somewhere() -> None:
    assert LOCKED.exists() or SEALED.exists()


def test_required_facts_and_sources_match_the_records() -> None:
    known = facts()
    packs = [pack(TUNING)] + ([pack(LOCKED)] if LOCKED.exists() else [])
    for data in packs:
        for q in data["questions"]:
            sources = {
                (s["file"], json.dumps(s["location"], sort_keys=True))
                for s in q["required_sources"]
            }
            for fact_id in q["required_facts"]:
                fact = known[fact_id]
                assert fact["client"] == q["client"], q["id"]
                location = fact.get("location") or {"file": fact["files"][0], "image": True}
                assert any(file == location["file"] for file, _ in sources), (q["id"], fact_id)
            for file, _ in sources:
                assert file.startswith(f"Test data (fictional)/Clients/{q['client']}/"), q["id"]
            if q["category"] == "unanswerable":
                assert (
                    q["required_facts"] == []
                    and q["expected_answer"] == questions.NOT_FOUND[q["language"]]
                )
            else:
                assert q["required_facts"], q["id"]
            assert (q["mode"] == "summarise") == (q["category"] == "summary")


def test_schema_rejects_a_broken_question() -> None:
    data = pack(TUNING)
    broken = dict(data["questions"][0])
    del broken["expected_answer"]
    with pytest.raises(jsonschema.ValidationError):
        questions.validate({**data, "questions": [broken]})
