"""T20.AC2–AC4: interruption, re-runs and tombstoned clients."""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from ingest_helpers import fake_target, fresh_db, plain_read, vtt

from company_brain import ingest
from company_brain.embedding import load_embedding_models

pytestmark = pytest.mark.integration
MODEL = load_embedding_models()["bge-m3:latest"]


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    connection = fresh_db()
    yield connection
    connection.close()


def write(root: Path, relative: str, text: str) -> None:
    (root / relative).parent.mkdir(parents=True, exist_ok=True)
    (root / relative).write_text(vtt(text), encoding="utf-8")


def test_rerun_without_changes_embeds_nothing(tmp_path: Path, conn: psycopg.Connection) -> None:
    """T20.AC3."""
    target = fake_target(conn, MODEL, "d1", 500)
    write(tmp_path, "Clients/Acme/Reports/a.vtt", "The fee is EUR 48,000.")
    write(tmp_path, "Clients/Acme/Transcripts/b.vtt", "Dana: the pilot starts in Leeds.")
    first = ingest.ingest(conn, tmp_path, [target], plain_read(tmp_path))
    assert first.state == "completed" and first.added == 2
    before = target.embed.calls  # type: ignore[attr-defined]
    second = ingest.ingest(conn, tmp_path, [target], plain_read(tmp_path))
    assert second.state == "completed" and (second.added, second.changed) == (0, 0)
    assert target.embed.calls == before  # type: ignore[attr-defined]
    row = conn.execute(
        "SELECT indexed_up_to FROM brain.ingest_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    assert row is not None and row[0] is not None


def test_interruption_leaves_old_or_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conn: psycopg.Connection
) -> None:
    """T20.AC2: a crash while writing a file's chunks leaves the file fully old in that index."""
    target = fake_target(conn, MODEL, "d1", 300)
    long_text = " ".join(f"Sentence {n} about the Leeds pilot and its fee." for n in range(400))
    write(tmp_path, "Clients/Acme/Reports/a.vtt", long_text)
    ingest.ingest(conn, tmp_path, [target], plain_read(tmp_path))
    old = ingest.indexed(conn, target.index_id)
    old_chunks = conn.execute("SELECT count(*) FROM brain.chunks").fetchone()
    write(tmp_path, "Clients/Acme/Reports/a.vtt", long_text.replace("Leeds", "Hull"))
    real_insert, calls = ingest._insert_chunk, {"n": 0}

    def crash(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt("power cut")
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(ingest, "_insert_chunk", crash)
    with pytest.raises(KeyboardInterrupt):
        ingest.ingest(conn, tmp_path, [target], plain_read(tmp_path))
    conn.rollback()
    assert ingest.indexed(conn, target.index_id) == old
    assert conn.execute("SELECT count(*) FROM brain.chunks").fetchone() == old_chunks
    assert conn.execute(
        "SELECT count(*) FROM brain.chunks WHERE text LIKE '%Hull%'"
    ).fetchone() == (0,)
    monkeypatch.setattr(ingest, "_insert_chunk", real_insert)
    ingest.ingest(conn, tmp_path, [target], plain_read(tmp_path))
    new_hash = hashlib.sha256((tmp_path / "Clients/Acme/Reports/a.vtt").read_bytes()).hexdigest()
    assert ingest.indexed(conn, target.index_id) == {"Clients/Acme/Reports/a.vtt": new_hash}
    assert conn.execute(
        "SELECT count(*) FROM brain.chunks WHERE text LIKE '%Leeds%'"
    ).fetchone() == (0,)


def test_tombstoned_clients_are_skipped(tmp_path: Path, conn: psycopg.Connection) -> None:
    """T20.AC4."""
    target = fake_target(conn, MODEL, "d1", 500)
    write(tmp_path, "Clients/Acme/Reports/a.vtt", "Acme report.")
    write(tmp_path, "Clients/Gone/Reports/b.vtt", "Gone report.")
    conn.execute("INSERT INTO brain.tombstones (client) VALUES ('Gone')")
    conn.commit()
    result = ingest.ingest(conn, tmp_path, [target], plain_read(tmp_path))
    assert set(ingest.indexed(conn, target.index_id)) == {"Clients/Acme/Reports/a.vtt"}
    assert result.added == 1


def test_transient_errors_are_retried_and_failures_recorded(
    tmp_path: Path, conn: psycopg.Connection
) -> None:
    target = fake_target(conn, MODEL, "d1", 500)
    write(tmp_path, "Clients/Acme/Reports/a.vtt", "Acme report.")
    write(tmp_path, "Clients/Acme/Reports/b.vtt", "Another report.")
    real, attempts = target.embed, {"n": 0}

    def flaky(texts: list[str]) -> list[list[float]]:
        attempts["n"] += 1
        if "Another" in texts[0]:
            raise ConnectionError("model busy")
        if attempts["n"] == 1:
            raise TimeoutError("first call times out")
        return real(texts)

    flaky_target = ingest.Target(target.index_id, target.budget, target.model, flaky)
    result = ingest.ingest(
        conn, tmp_path, [flaky_target], plain_read(tmp_path), sleep=lambda _s: None
    )
    assert set(ingest.indexed(conn, target.index_id)) == {"Clients/Acme/Reports/a.vtt"}
    assert result.failed == 1 and result.state == "completed"
    status = conn.execute(
        "SELECT f.status FROM brain.index_files f JOIN brain.documents d USING (doc_id) "
        "WHERE d.rel_path LIKE '%b.vtt'"
    ).fetchone()
    assert status == ("failed",)


def test_no_deletions_after_an_incomplete_scan_or_missing_root(
    tmp_path: Path, conn: psycopg.Connection
) -> None:
    """T20.AC1 (direct case): a folder that cannot be listed, or a missing root, deletes nothing."""
    from company_brain import scanner

    target = fake_target(conn, MODEL, "d1", 500)
    root = tmp_path / "root"
    write(root, "Clients/Acme/Reports/a.vtt", "Acme report.")
    write(root, "Clients/Brisa/Reports/b.vtt", "Brisa report.")
    ingest.ingest(conn, root, [target], plain_read(root))
    both = ingest.indexed(conn, target.index_id)
    assert len(both) == 2

    def broken(path: Path, **kwargs):  # type: ignore[no-untyped-def]
        report = scanner.scan(path, **kwargs)
        report.files = [f for f in report.files if f.client != "Acme"]
        report.errors.append((Path("Clients/Acme"), "could not be listed"))
        return report

    result = ingest.ingest(conn, root, [target], plain_read(root), scan_fn=broken)
    assert result.deletions_skipped and ingest.indexed(conn, target.index_id) == both
    root.rename(tmp_path / "away")
    assert ingest.ingest(conn, root, [target], plain_read(root)).state == "failed"
    assert ingest.indexed(conn, target.index_id) == both


def test_stopping_between_files_deletes_nothing(tmp_path: Path, conn: psycopg.Connection) -> None:
    """T20U: a stop request ends the run at a file boundary; no deletion on a stopped run."""
    target = fake_target(conn, MODEL, "d1", 500)
    write(tmp_path, "Clients/Acme/Reports/a.vtt", "First report.")
    write(tmp_path, "Clients/Acme/Reports/b.vtt", "Second report.")
    ingest.ingest(conn, tmp_path, [target], plain_read(tmp_path))
    (tmp_path / "Clients/Acme/Reports/b.vtt").unlink()
    write(tmp_path, "Clients/Acme/Reports/c.vtt", "Third report.")
    result = ingest.ingest(conn, tmp_path, [target], plain_read(tmp_path), should_stop=lambda: True)
    assert result.stopped and result.state == "failed" and result.removed == 0
    assert set(ingest.indexed(conn, target.index_id)) == {
        "Clients/Acme/Reports/a.vtt",
        "Clients/Acme/Reports/b.vtt",
    }
