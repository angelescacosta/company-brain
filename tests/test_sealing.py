"""T14S: sealing the locked questions (with a test key in the test Keychain prefix)."""

import json
from pathlib import Path

import pytest

from company_brain import seal

LOCKED = {
    "split": "locked",
    "seed": 1,
    "questions": [
        {
            "id": "q-en-01",
            "question": "What fee did we agree with Northwind Bakery in the signed agreement?",
        },
        {"id": "q-es-02", "question": "¿Cuándo empieza el contrato con Ferretería Soto?"},
    ],
}


@pytest.fixture
def sealed(tmp_path: Path):  # type: ignore[no-untyped-def]
    plain = tmp_path / "review" / "locked.json"
    plain.parent.mkdir()
    plain.write_text(json.dumps(LOCKED, ensure_ascii=False), encoding="utf-8")
    log = tmp_path / "seal_access.log"
    account = f"test-{tmp_path.name}"
    out = tmp_path / "locked.json.age"
    digest = seal.seal(plain, out, account=account, log=log)
    yield plain, out, digest, log, account
    seal.forget_key(account)


def test_the_sealed_file_opens_to_exactly_the_reviewed_questions(sealed) -> None:  # type: ignore[no-untyped-def]
    """T14S.AC1."""
    plain, out, digest, log, account = sealed
    assert b"Northwind" not in out.read_bytes()
    assert seal.open_sealed(out, "test", account=account, log=log) == LOCKED
    assert len(digest) == 64


def test_every_key_read_is_logged(sealed) -> None:  # type: ignore[no-untyped-def]
    """T14S.AC3: the guard logs purpose and time for every read, nothing else."""
    _plain, out, _digest, log, account = sealed
    before = log.read_text().count("\n")
    seal.open_sealed(out, "final evaluation (T38)", account=account, log=log)
    lines = log.read_text().splitlines()
    assert len(lines) == before + 1 and "final evaluation (T38)" in lines[-1]
    assert "Northwind" not in log.read_text()


def test_plain_copies_are_found(tmp_path: Path) -> None:
    """T14S.AC2: the check finds any file still holding a locked question's text."""
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "clean.txt").write_text("nothing here")
    texts = [q["question"] for q in LOCKED["questions"]]
    assert seal.plain_copies(texts, [folder]) == []
    (folder / "copy.json").write_text(json.dumps(LOCKED, ensure_ascii=False))
    assert seal.plain_copies(texts, [folder]) == [folder / "copy.json"]
