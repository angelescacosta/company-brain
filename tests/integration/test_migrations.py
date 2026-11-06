"""T5.AC1: migrations apply to an empty database and re-running changes nothing.

Runs as `brain_test` on `company_brain_test`, in the `brain` schema it owns.
"""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from company_brain.db import connect, migrate

pytestmark = pytest.mark.integration

TABLES = {
    "documents",
    "chunks",
    "index_meta",
    "index_files",
    "ingest_runs",
    "file_status",
    "tombstones",
    "chat_bindings",
    "schema_migrations",
}


@pytest.fixture
def empty_db() -> Iterator[psycopg.Connection]:
    with connect() as conn:
        # The role owns the `brain` schema but may not create schemas, so empty it.
        tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'brain'")
        for (table,) in tables.fetchall():
            conn.execute(
                sql.SQL("DROP TABLE IF EXISTS brain.{} CASCADE").format(sql.Identifier(table))
            )
        conn.commit()
        yield conn


def snapshot(conn: psycopg.Connection) -> list[tuple[object, ...]]:
    rows = conn.execute(
        """
        SELECT 'column', table_name, column_name, data_type, is_nullable,
               coalesce(column_default, ''), coalesce(generation_expression, '')
        FROM information_schema.columns WHERE table_schema = 'brain'
        UNION ALL
        SELECT 'constraint', conrelid::regclass::text, conname, pg_get_constraintdef(oid),
               '', '', ''
        FROM pg_constraint WHERE connamespace = 'brain'::regnamespace
        UNION ALL
        SELECT 'index', tablename, indexname, indexdef, '', '', ''
        FROM pg_indexes WHERE schemaname = 'brain'
        UNION ALL
        SELECT 'migration', version, '', '', '', '', '' FROM brain.schema_migrations
        ORDER BY 1, 2, 3
        """
    ).fetchall()
    return [tuple(row) for row in rows]


def test_apply_to_empty_database(empty_db: psycopg.Connection) -> None:
    applied = migrate(empty_db)
    assert applied == ["0001_initial", "0002_chat_bindings"]
    tables = {
        r[0]
        for r in empty_db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'brain'"
        )
    }
    assert tables == TABLES


def test_rerun_changes_nothing(empty_db: psycopg.Connection) -> None:
    migrate(empty_db)
    before = snapshot(empty_db)
    assert migrate(empty_db) == []
    assert snapshot(empty_db) == before


def _index(conn: psycopg.Connection, index_id: str, active: bool, dims: int) -> None:
    conn.execute(
        """INSERT INTO index_meta (index_id, model, revision, dimensions, instruction,
                                   chunk_budget, active, complete)
           VALUES (%s, 'm', 'r', %s, '', 500, %s, true)""",
        (index_id, dims, active),
    )


def test_only_one_active_index(empty_db: psycopg.Connection) -> None:
    migrate(empty_db)
    _index(empty_db, "a" * 8, True, 3)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _index(empty_db, "b" * 8, True, 4)


def test_untyped_vectors_and_language_search(empty_db: psycopg.Connection) -> None:
    migrate(empty_db)
    _index(empty_db, "a" * 8, False, 3)
    _index(empty_db, "b" * 8, False, 4)
    empty_db.execute(
        """INSERT INTO documents (doc_id, client, scope, doc_type, language, status,
                                  file_hash, availability, rel_path)
           VALUES ('d1', 'lumen', 'fictional:lumen', 'contract', 'es', 'signed',
                   'h', 'available', 'x.docx')"""
    )
    for index_id, vector in (("a" * 8, "[1,0,0]"), ("b" * 8, "[0,1,0,0]")):
        empty_db.execute(
            """INSERT INTO chunks (chunk_id, index_id, doc_id, text, label, embedding)
               VALUES (%s, %s, 'd1', 'Los contratos firmados incluyen honorarios.', 'l', %s)""",
            (index_id + "c", index_id, vector),
        )
    found = empty_db.execute(
        """SELECT count(*) FROM chunks
           WHERE tsv_es @@ to_tsquery('spanish', 'contrato')
             AND tsv_simple @@ to_tsquery('simple', 'honorarios')"""
    ).fetchone()
    assert found == (2,)


def test_status_values_are_checked(empty_db: psycopg.Connection) -> None:
    migrate(empty_db)
    with pytest.raises(psycopg.errors.CheckViolation):
        empty_db.execute(
            """INSERT INTO documents (doc_id, client, scope, doc_type, language, status,
                                      file_hash, availability, rel_path)
               VALUES ('d1', 'c', 'c', 't', 'en', 'maybe', 'h', 'available', 'x')"""
        )
