"""T42: deleting a client, and reconciling restored data with the tombstone list.

Marker `destructive`. PBT: for any generated multi-client dataset, deleting client X
removes every X-owned record and nothing else; reconciliation with any tombstone
list removes exactly the tombstoned clients' records (protects other clients'
data and keeps a deleted client deleted after a restore).
"""

import json
import sqlite3
import sys
from pathlib import Path

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from company_brain import client_delete as cd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest_helpers import fresh_db, vtt  # noqa: E402

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
CLIENTS = ["Acme", "Brisa", "Cobalt"]


def scope(client: str) -> str:
    return f"fictional:{client}"


def webui(path: Path) -> Path:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE chat (id TEXT PRIMARY KEY, title TEXT)")
    for table in ("chat_message", "chat_file", "chatidtag", "shared_chat"):
        db.execute(f"CREATE TABLE {table} (id TEXT, chat_id TEXT)")
    db.commit()
    db.close()
    return path


def populate(conn: psycopg.Connection, places: cd.Places, owners: list[str]) -> None:
    """One document, chunk, chat, export, file status and evaluation line per entry in `owners`."""
    db = sqlite3.connect(places.webui_db)
    places.exports_dir.mkdir(parents=True, exist_ok=True)
    exports: dict[str, str] = {}
    lines = []
    conn.execute("""INSERT INTO brain.index_meta (index_id, model, revision, dimensions,
                    instruction, chunk_budget) VALUES ('i', 'm', 'r', 4, '', 500)
                    ON CONFLICT DO NOTHING""")
    for n, client in enumerate(owners):
        doc = f"d{n}"
        path = f"Test data (fictional)/Clients/{client}/Reports/r{n}.vtt"
        conn.execute(
            """INSERT INTO brain.documents (doc_id, client, scope, doc_type, language,
                        file_hash, availability, rel_path)
                        VALUES (%s, %s, %s, 'report', 'en', 'h', 'available', %s)""",
            (doc, client, scope(client), path),
        )
        conn.execute(
            "INSERT INTO brain.chunks (chunk_id, index_id, doc_id, text, label) "
            "VALUES (%s, 'i', %s, 't', 'l')",
            (f"c{n}", doc),
        )
        conn.execute(
            "INSERT INTO brain.chat_bindings (chat_id, scope) VALUES (%s, %s)",
            (f"chat{n}", scope(client)),
        )
        conn.execute(
            "INSERT INTO brain.file_status (rel_path, status, reason, size) "
            "VALUES (%s, 'unavailable', 'r', 1)",
            (path + ".x",),
        )
        db.execute("INSERT INTO chat (id, title) VALUES (?, ?)", (f"chat{n}", "t"))
        for table in ("chat_message", "chat_file", "chatidtag", "shared_chat"):
            sql = f"INSERT INTO {table} (id, chat_id) VALUES (?, ?)"  # noqa: S608 - fixed names
            db.execute(sql, (f"{table}{n}", f"chat{n}"))
        name = f"Summary {n}.docx"
        (places.exports_dir / name).write_text("x")
        exports[name] = scope(client)
        lines.append(json.dumps({"id": f"q{n}", "client": client}))
    conn.commit()
    db.commit()
    db.close()
    (places.exports_dir / cd.EXPORT_INDEX).write_text(json.dumps(exports))
    places.runs_dir.joinpath("r1").mkdir(parents=True, exist_ok=True)
    (places.runs_dir / "r1" / "questions.jsonl").write_text("\n".join(lines) + "\n")


def owned(conn: psycopg.Connection, places: cd.Places) -> dict[str, set[str]]:
    """What each scope still owns, across every store."""
    found: dict[str, set[str]] = {}

    def add(owner: str, item: str) -> None:
        found.setdefault(owner, set()).add(item)

    for doc, sc in conn.execute("SELECT doc_id, scope FROM brain.documents"):
        add(sc, f"doc:{doc}")
    for chunk, sc in conn.execute(
        "SELECT c.chunk_id, d.scope FROM brain.chunks c JOIN brain.documents d USING (doc_id)"
    ):
        add(sc, f"chunk:{chunk}")
    bindings = dict(conn.execute("SELECT chat_id, scope FROM brain.chat_bindings").fetchall())
    for chat, sc in bindings.items():
        add(sc, f"binding:{chat}")
    for (path,) in conn.execute("SELECT rel_path FROM brain.file_status"):
        add(scope(path.split("/")[2]), f"status:{path}")
    conn.commit()
    db = sqlite3.connect(places.webui_db)
    for (chat,) in db.execute("SELECT chat_id FROM chat_message"):
        add(bindings.get(chat, f"orphan:{chat}"), f"message:{chat}")
    db.close()
    index = json.loads((places.exports_dir / cd.EXPORT_INDEX).read_text())
    for name, sc in index.items():
        if (places.exports_dir / name).exists():
            add(sc, f"export:{name}")
    for line in (places.runs_dir / "r1" / "questions.jsonl").read_text().splitlines():
        record = json.loads(line)
        add(scope(record["client"]), f"eval:{record['id']}")
    return found


@pytest.fixture
def places(tmp_path: Path) -> cd.Places:
    return cd.Places(
        webui_db=webui(tmp_path / "webui.db"),
        exports_dir=tmp_path / "exports",
        runs_dir=tmp_path / "runs",
        markdown_dir=tmp_path / "markdown",
        tombstones_file=tmp_path / "tombstones.json",
        backups_dir=tmp_path / "backups",
        recipient=None,
    )


def test_delete_one_client(places: cd.Places, tmp_path: Path) -> None:
    """T42.AC1 and AC2."""
    conn = fresh_db()
    populate(conn, places, ["Acme", "Brisa", "Cobalt", "Brisa"])
    before = owned(conn, places)
    report = cd.delete_client(conn, scope("Brisa"), places)
    after = owned(conn, places)
    assert scope("Brisa") not in after
    assert after[scope("Acme")] == before[scope("Acme")]
    assert after[scope("Cobalt")] == before[scope("Cobalt")]
    assert cd.tombstone_id(scope("Brisa")) in json.loads(places.tombstones_file.read_text())["ids"]
    assert "Brisa" not in places.tombstones_file.read_text()  # IDs only, never the name
    assert "Drive" in report.message
    from ingest_helpers import fake_target, plain_read

    from company_brain import ingest
    from company_brain.embedding import load_embedding_models

    root = tmp_path / "root"
    target_file = root / "Test data (fictional)/Clients/Brisa/Reports/new.vtt"
    target_file.parent.mkdir(parents=True)
    target_file.write_text(vtt("Brisa report"))
    target = fake_target(conn, load_embedding_models()["bge-m3:latest"], "d9", 500)
    ingest.ingest(conn, root, [target], plain_read(root))
    assert scope("Brisa") not in owned(conn, places)  # ingestion does not bring it back
    conn.close()


datasets = st.lists(st.sampled_from(CLIENTS), min_size=1, max_size=8)


@settings(
    max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(datasets, st.sampled_from(CLIENTS))
def test_delete_removes_exactly_that_client(
    places: cd.Places, owners: list[str], victim: str
) -> None:
    conn = fresh_db()
    for path in (places.exports_dir, places.runs_dir):
        if path.exists():
            for item in sorted(path.rglob("*"), reverse=True):
                item.unlink() if item.is_file() else item.rmdir()
    webui(places.webui_db.with_suffix(".tmp"))
    places.webui_db.with_suffix(".tmp").replace(places.webui_db)
    populate(conn, places, owners)
    before = owned(conn, places)
    cd.delete_client(conn, scope(victim), places)
    after = owned(conn, places)
    assert {k: v for k, v in before.items() if k != scope(victim)} == after
    conn.close()


@settings(
    max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(datasets, st.sets(st.sampled_from(CLIENTS)))
def test_reconcile_removes_exactly_the_tombstoned(
    places: cd.Places, owners: list[str], gone: set[str]
) -> None:
    """T42.AC3: a restored dataset loses exactly the tombstoned clients."""
    conn = fresh_db()
    for path in (places.exports_dir, places.runs_dir):
        if path.exists():
            for item in sorted(path.rglob("*"), reverse=True):
                item.unlink() if item.is_file() else item.rmdir()
    webui(places.webui_db.with_suffix(".tmp"))
    places.webui_db.with_suffix(".tmp").replace(places.webui_db)
    populate(conn, places, owners)
    before = owned(conn, places)
    cd.reconcile(conn, {cd.tombstone_id(scope(c)) for c in gone}, places)
    after = owned(conn, places)
    assert after == {k: v for k, v in before.items() if k not in {scope(c) for c in gone}}
    conn.close()
