"""T40: backup, wipe and restore on the test copies; wrong key, damage, failed switch-over,
a client deleted after the backup, and a missing tombstone list.

Marker `destructive`. PBT: Hypothesis-generated small databases (Unicode text, empty
tables) survive the round trip unchanged (covers edge cases a fixed dump would miss).
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from company_brain import backup, client_delete, restore, signing
from company_brain.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest_helpers import fresh_db  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
AGE_DIR = Path.home() / ".local" / "opt" / "age-1.3.2"


def new_key() -> tuple[str, str]:
    out = subprocess.run([str(AGE_DIR / "age-keygen")], capture_output=True, text=True, check=True)
    identity = next(line for line in out.stdout.splitlines() if line.startswith("AGE-SECRET-KEY"))
    recipient = subprocess.run(
        [str(AGE_DIR / "age-keygen"), "-y"],
        input=identity + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return identity, recipient


IDENTITY, RECIPIENT = new_key()
KEY = signing.key_from_identity(IDENTITY)  # what Set up backups keeps in the Keychain


def places(root: Path, recipient: str | None = RECIPIENT) -> client_delete.Places:
    return client_delete.Places(
        webui_db=Settings.from_env().data_dir / "openwebui" / "webui.db",
        exports_dir=root / "exports",
        runs_dir=root / "runs",
        markdown_dir=root / "markdown",
        tombstones_file=root / "tombstones.json",
        backups_dir=root / "backups",
        recipient=recipient,
        signing_key=KEY,
    )


def canonical(conn: psycopg.Connection) -> dict[str, list[str]]:
    """Every brain table's rows as text, sorted (volatile columns are restored as they were)."""
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'brain' ORDER BY 1"
        )
    ]
    dump = {
        t: sorted(r[0] for r in conn.execute(f"SELECT t::text FROM brain.{t} t"))  # noqa: S608
        for t in tables
    }
    conn.commit()
    return dump


def webui_rows() -> list[str]:
    path = Settings.from_env().data_dir / "openwebui" / "webui.db"
    db = sqlite3.connect(path)
    rows = list(db.iterdump())
    db.close()
    return rows


def seed(conn: psycopg.Connection, docs: list[tuple[str, str]], chats: list[str]) -> None:
    conn.execute(
        "INSERT INTO brain.index_meta (index_id, model, revision, dimensions, "
        "instruction, chunk_budget) VALUES ('i', 'm', 'r', 4, '', 500)"
    )
    for n, (client, text) in enumerate(docs):
        conn.execute(
            "INSERT INTO brain.documents (doc_id, client, scope, doc_type, language, file_hash, "
            "availability, rel_path) VALUES (%s, %s, %s, 'report', 'en', 'h', 'available', %s)",
            (
                f"d{n}",
                client,
                f"fictional:{client}",
                f"Test data (fictional)/Clients/{client}/Reports/r{n}.vtt",
            ),
        )
        conn.execute(
            "INSERT INTO brain.chunks (chunk_id, index_id, doc_id, text, label) "
            "VALUES (%s, 'i', %s, %s, 'l')",
            (f"c{n}", f"d{n}", text),
        )
    for n, client in enumerate(chats):
        conn.execute(
            "INSERT INTO brain.chat_bindings (chat_id, scope) VALUES (%s, %s)",
            (f"chat{n}", f"fictional:{client}"),
        )
    conn.commit()
    path = Settings.from_env().data_dir / "openwebui" / "webui.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE chat (id TEXT PRIMARY KEY, title TEXT)")
    for table in client_delete.WEBUI_TABLES:
        db.execute(f"CREATE TABLE {table} (id TEXT, chat_id TEXT)")
    for n, _client in enumerate(chats):
        db.execute("INSERT INTO chat VALUES (?, ?)", (f"chat{n}", f"título {n} ✓"))
        db.execute("INSERT INTO chat_message VALUES (?, ?)", (f"m{n}", f"chat{n}"))
    db.commit()
    db.close()


def make_backup(root: Path) -> Path:
    folder = root / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    listed = root / "tombstones.json"
    if not listed.exists():  # the committed list, empty until a client is deleted
        listed.write_text('{"ids": []}')
    result = backup.run(
        Settings.from_env(), RECIPIENT, folder, services=backup.NoServices(), signing_key=KEY
    )
    assert result.state == "completed", result
    assert result.archive is not None
    return result.archive


def wipe(conn: psycopg.Connection) -> None:
    fresh_db().close()
    path = Settings.from_env().data_dir / "openwebui" / "webui.db"
    path.unlink(missing_ok=True)


clients = st.sampled_from(["Acme", "Brisa", "Ñandú"])
texts = st.text(
    st.characters(blacklist_categories=["Cs"], blacklist_characters="\x00"), max_size=40
)


@settings(
    max_examples=6, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.lists(st.tuples(clients, texts), max_size=4), st.lists(clients, max_size=3))
def test_round_trip(tmp_path_factory: pytest.TempPathFactory, docs, chats) -> None:  # type: ignore[no-untyped-def]
    """T40.AC1: backup, wipe and restore give identical data."""
    root = tmp_path_factory.mktemp("rt")
    conn = fresh_db()
    seed(conn, docs, chats)
    before, webui_before = canonical(conn), webui_rows()
    archive = make_backup(root)
    wipe(conn)
    conn.close()
    result = restore.restore(
        Settings.from_env(), archive, IDENTITY, places(root), services=backup.NoServices()
    )
    assert result.state == "restored", result
    conn = fresh_conn()
    assert canonical(conn) == before
    assert webui_rows() == webui_before
    conn.close()


def fresh_conn() -> psycopg.Connection:
    from company_brain.db import connect

    return connect()


def test_wrong_key_or_damage_changes_nothing(tmp_path: Path) -> None:
    """T40.AC2."""
    conn = fresh_db()
    seed(conn, [("Acme", "first")], ["Acme"])
    archive = make_backup(tmp_path)
    conn.execute("UPDATE brain.chunks SET text = 'after the backup'")
    conn.commit()
    before, webui_before = canonical(conn), webui_rows()
    wrong, _ = new_key()
    result = restore.restore(
        Settings.from_env(), archive, wrong, places(tmp_path), services=backup.NoServices()
    )
    assert result.state == "failed" and "Nothing was changed" in result.message
    damaged = tmp_path / "damaged.tar.age"
    data = bytearray(archive.read_bytes())
    data[-50] ^= 0xFF
    damaged.write_bytes(bytes(data))
    result = restore.restore(
        Settings.from_env(), damaged, IDENTITY, places(tmp_path), services=backup.NoServices()
    )
    assert result.state == "failed" and "Nothing was changed" in result.message
    assert "Traceback" not in result.message
    assert canonical(conn) == before and webui_rows() == webui_before
    conn.close()


def test_failed_switch_keeps_the_previous_data(tmp_path: Path) -> None:
    """T40.AC3."""
    conn = fresh_db()
    seed(conn, [("Acme", "in the backup")], ["Acme"])
    archive = make_backup(tmp_path)
    conn.execute("UPDATE brain.chunks SET text = 'current data'")
    conn.commit()
    before, webui_before = canonical(conn), webui_rows()
    conn.close()

    def fail() -> None:
        raise OSError("the switch failed")

    result = restore.restore(
        Settings.from_env(),
        archive,
        IDENTITY,
        places(tmp_path),
        services=backup.NoServices(),
        before_switch=fail,
    )
    assert result.state == "failed"
    conn = fresh_conn()
    assert canonical(conn) == before and webui_rows() == webui_before
    conn.close()


def test_a_deleted_client_does_not_come_back(tmp_path: Path) -> None:
    """T40.AC4: the archive predates the deletion; the tombstone list removes the client."""
    conn = fresh_db()
    seed(conn, [("Acme", "a"), ("Brisa", "b")], ["Acme", "Brisa"])
    archive = make_backup(tmp_path)
    client_delete.delete_client(conn, "fictional:Brisa", places(tmp_path))
    conn.close()
    assert (tmp_path / "backups" / "tombstones.age").exists()
    (tmp_path / "tombstones.json").unlink()  # only the encrypted copy next to the backups is left
    result = restore.restore(
        Settings.from_env(), archive, IDENTITY, places(tmp_path), services=backup.NoServices()
    )
    assert result.state == "restored", result
    conn = fresh_conn()
    scopes = {r[0] for r in conn.execute("SELECT scope FROM brain.documents")}
    bound = {r[0] for r in conn.execute("SELECT scope FROM brain.chat_bindings")}
    assert scopes == bound == {"fictional:Acme"}
    assert not any("chat1" in row for row in webui_rows())
    conn.close()


def test_no_tombstone_list_asks_first(tmp_path: Path) -> None:
    """T40.AC5: neither the committed list nor the encrypted copy next to the backups is found
    (the archive's own list may be older than the last deletion, so it does not count)."""
    conn = fresh_db()
    seed(conn, [("Acme", "a")], [])
    archive = make_backup(tmp_path)
    conn.execute("UPDATE brain.chunks SET text = 'current'")
    conn.commit()
    before = canonical(conn)
    conn.close()
    no_list = places(tmp_path / "elsewhere")
    result = restore.restore(
        Settings.from_env(), archive, IDENTITY, no_list, services=backup.NoServices()
    )
    assert result.state == "needs confirmation" and "confirm" in result.message
    conn = fresh_conn()
    assert canonical(conn) == before
    conn.close()
    result = restore.restore(
        Settings.from_env(),
        archive,
        IDENTITY,
        no_list,
        services=backup.NoServices(),
        confirmed=True,
    )
    assert result.state == "restored"


def test_a_backup_not_signed_by_this_assistant_is_refused(tmp_path: Path) -> None:
    """Security scan F1: anyone can encrypt to the public recipient, so a backup someone else
    made (signed with another key, as a forger must) opens but is refused; nothing changes."""
    conn = fresh_db()
    seed(conn, [("Acme", "first")], ["Acme"])
    folder = tmp_path / "backups"
    folder.mkdir()
    (tmp_path / "tombstones.json").write_text('{"ids": []}')
    forger = signing.key_from_identity(new_key()[0])
    forged = backup.run(
        Settings.from_env(), RECIPIENT, folder, services=backup.NoServices(), signing_key=forger
    ).archive
    assert forged is not None
    conn.execute("UPDATE brain.chunks SET text = 'after the backup'")
    conn.commit()
    before, webui_before = canonical(conn), webui_rows()
    result = restore.restore(
        Settings.from_env(), forged, IDENTITY, places(tmp_path), services=backup.NoServices()
    )
    assert result.state == "failed" and "not made by this assistant" in result.message
    assert "Nothing was changed" in result.message
    assert canonical(conn) == before and webui_rows() == webui_before
    conn.close()


def test_a_forged_deleted_clients_list_is_ignored(tmp_path: Path) -> None:
    """Security scan F1: the encrypted list next to the backups counts only when signed."""
    conn = fresh_db()
    seed(conn, [("Acme", "a"), ("Brisa", "b")], ["Acme", "Brisa"])
    archive = make_backup(tmp_path)
    conn.close()
    (tmp_path / "tombstones.json").unlink()
    plain = tmp_path / "forged.json"
    plain.write_text(json.dumps({"ids": [client_delete.tombstone_id("fictional:Acme")]}))
    subprocess.run(
        [
            str(AGE_DIR / "age"),
            "-r",
            RECIPIENT,
            "-o",
            str(tmp_path / "backups" / "tombstones.age"),
            str(plain),
        ],
        check=True,
    )
    result = restore.restore(
        Settings.from_env(),
        archive,
        IDENTITY,
        places(tmp_path),
        services=backup.NoServices(),
        confirmed=True,
    )
    assert result.state == "restored", result
    conn = fresh_conn()
    assert {r[0] for r in conn.execute("SELECT scope FROM brain.documents")} == {
        "fictional:Acme",
        "fictional:Brisa",
    }
    conn.close()


def test_restore_keeps_only_this_projects_pipe(tmp_path: Path) -> None:
    """Security scan F1, defence in depth: Open WebUI runs the code of the Functions and Tools
    in its database, so after a restore only the Company Brain Pipe stays, from current source."""
    conn = fresh_db()
    seed(conn, [("Acme", "a")], ["Acme"])
    path = Settings.from_env().data_dir / "openwebui" / "webui.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE function (id TEXT PRIMARY KEY, content TEXT, valves TEXT)")
    db.execute("CREATE TABLE tool (id TEXT PRIMARY KEY, content TEXT)")
    db.execute("INSERT INTO function VALUES ('company_brain', 'old source', '{}')")
    db.execute("INSERT INTO function VALUES ('other', 'import os', '{}')")
    db.execute("INSERT INTO tool VALUES ('t', 'import os')")
    db.commit()
    db.close()
    archive = make_backup(tmp_path)
    conn.close()
    result = restore.restore(
        Settings.from_env(), archive, IDENTITY, places(tmp_path), services=backup.NoServices()
    )
    assert result.state == "restored", result
    db = sqlite3.connect(path)
    functions = db.execute("SELECT id, content FROM function").fetchall()
    tools = db.execute("SELECT id FROM tool").fetchall()
    db.close()
    from company_brain import pipe_install

    assert functions == [("company_brain", pipe_install.SOURCE.read_text(encoding="utf-8"))]
    assert tools == []
