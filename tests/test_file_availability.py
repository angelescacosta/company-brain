"""T16: pointer files and unavailable files are listed, never converted, and a
previously indexed file that becomes unavailable keeps its chunks."""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from company_brain.availability import (
    SF_DATALESS,
    FileStatus,
    apply_statuses,
    check_availability,
)
from company_brain.db import connect, migrate
from company_brain.scanner import scan


def touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_pointers_listed_and_not_passed_on(tmp_path: Path) -> None:
    root = tmp_path / "root"
    touch(root / "Clients" / "acme" / "Proposals" / "draft.gdoc", b'{"url": "x"}')
    touch(root / "Clients" / "acme" / "Reports" / "plan.gsheet")
    touch(root / "Methodology" / "deck.gslides")
    touch(root / "Methodology" / "m.pdf")
    available, statuses = check_availability(scan(root))
    assert [f.path.name for f in available] == ["m.pdf"]
    pointers = {s.rel_path: s for s in statuses if s.status == "pointer"}
    assert set(pointers) == {
        "Clients/acme/Proposals/draft.gdoc",
        "Clients/acme/Reports/plan.gsheet",
        "Methodology/deck.gslides",
    }
    assert all("Google" in s.reason for s in pointers.values())


def test_online_only_placeholders_listed_and_not_passed_on(tmp_path: Path) -> None:
    root = tmp_path / "root"
    placeholder = touch(root / "Methodology" / "online.pdf")
    touch(root / "Methodology" / "local.pdf")
    real_stat = os.stat

    def fake_stat(path: str | os.PathLike[str]) -> os.stat_result:
        result = real_stat(path)
        if Path(path) == placeholder.resolve():
            values = list(result)
            return os.stat_result(values, {"st_flags": result.st_flags | SF_DATALESS})
        return result

    available, statuses = check_availability(scan(root), stat=fake_stat)
    assert [f.path.name for f in available] == ["local.pdf"]
    (status,) = statuses
    assert (status.rel_path, status.status) == ("Methodology/online.pdf", "unavailable")
    assert "Available offline" in status.reason


def test_unreadable_files_listed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    locked = touch(root / "Methodology" / "locked.pdf")
    locked.chmod(0o000)
    try:
        available, statuses = check_availability(scan(root))
    finally:
        locked.chmod(0o644)
    assert available == []
    assert [(s.status, s.size) for s in statuses] == [("unavailable", 1)]


def test_reasons_are_plain_english(tmp_path: Path) -> None:
    root = tmp_path / "root"
    touch(root / "Methodology" / "a.gdoc")
    touch(root / "Methodology" / "notes.txt")
    _, statuses = check_availability(scan(root))
    for status in statuses:
        assert status.reason[0].isupper() and status.reason.endswith(".")
        assert "Error" not in status.reason and "Traceback" not in status.reason


@pytest.fixture
def db() -> Iterator[psycopg.Connection]:
    with connect() as conn:
        tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'brain'")
        for (table,) in tables.fetchall():
            conn.execute(
                sql.SQL("DROP TABLE IF EXISTS brain.{} CASCADE").format(sql.Identifier(table))
            )
        conn.commit()
        migrate(conn)
        yield conn


@pytest.mark.integration
def test_previously_indexed_file_keeps_its_chunks(db: psycopg.Connection) -> None:
    db.execute(
        """INSERT INTO index_meta (index_id, model, revision, dimensions, instruction,
                                   chunk_budget, active, complete)
           VALUES ('aaaaaaaa', 'm', 'r', 3, '', 500, true, true)"""
    )
    db.execute(
        """INSERT INTO documents (doc_id, client, scope, doc_type, language, file_hash,
                                  availability, rel_path)
           VALUES ('d1', 'shared', 'shared', 'methodology', 'en', 'h', 'available',
                   'Methodology/m.pdf')"""
    )
    db.execute(
        """INSERT INTO chunks (chunk_id, index_id, doc_id, text, label, embedding)
           VALUES ('c1', 'aaaaaaaa', 'd1', 'text', 'l', '[1,0,0]')"""
    )
    apply_statuses(
        db,
        [FileStatus("Methodology/m.pdf", "unavailable", "Online-only in Google Drive.", 10)],
    )
    db.commit()
    assert db.execute("SELECT availability FROM documents WHERE doc_id = 'd1'").fetchone() == (
        "unavailable",
    )
    assert db.execute("SELECT count(*) FROM chunks WHERE doc_id = 'd1'").fetchone() == (1,)
    assert db.execute("SELECT status, reason, size FROM file_status").fetchall() == [
        ("unavailable", "Online-only in Google Drive.", 10)
    ]


@pytest.mark.integration
def test_statuses_are_replaced_on_each_check(db: psycopg.Connection) -> None:
    apply_statuses(db, [FileStatus("Methodology/a.gdoc", "pointer", "Google file.", 1)])
    apply_statuses(db, [FileStatus("Methodology/b.gdoc", "pointer", "Google file.", 1)])
    db.commit()
    assert db.execute("SELECT rel_path FROM file_status").fetchall() == [("Methodology/b.gdoc",)]
