"""Shared helpers for the T20 ingestion tests: test database, fake embedder, plain reader."""

import hashlib
from pathlib import Path

import psycopg
from psycopg import sql

from company_brain import ingest
from company_brain.content_guard import VerifiedBytes
from company_brain.db import connect, migrate
from company_brain.embedding import EmbeddingModel, normalise, register_index

DIMENSIONS = 4


def fresh_db() -> psycopg.Connection:
    conn = connect()
    # A test that failed or was stopped can leave its own session holding locks;
    # end those first so emptying the schema never waits (test database only).
    conn.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = current_user "
        "AND datname = current_database() AND pid <> pg_backend_pid()"
    )
    conn.commit()
    tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'brain'")
    for (table,) in tables.fetchall():
        conn.execute(sql.SQL("DROP TABLE IF EXISTS brain.{} CASCADE").format(sql.Identifier(table)))
    conn.commit()
    migrate(conn)
    conn.commit()
    return conn


class FakeEmbedder:
    """Deterministic unit vectors from the text; counts every text it embeds."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls += len(texts)
        out = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            out.append(normalise([b - 127.5 for b in digest[:DIMENSIONS]]))
        return out


def fake_target(
    conn: psycopg.Connection, model: EmbeddingModel, digest: str, budget: int
) -> ingest.Target:
    index_id = register_index(
        conn, model, digest=digest, dimensions=DIMENSIONS, chunk_budget=budget
    )
    conn.commit()
    return ingest.Target(index_id=index_id, budget=budget, model=model, embed=FakeEmbedder())


def plain_read(root: Path):  # type: ignore[no-untyped-def]
    def read(path: Path) -> VerifiedBytes:
        data = path.read_bytes()
        return VerifiedBytes(
            relative=path.resolve().relative_to(root.resolve()).as_posix(),
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
        )

    return read


def vtt(text: str, speaker: str = "Dana") -> str:
    """A one-cue WebVTT transcript (a supported type that converts without Docling)."""
    return f"WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n<v {speaker}>{text}\n"
