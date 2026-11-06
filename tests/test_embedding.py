"""T19.AC2–AC4 with a fake Ollama: prefixes, batching, mismatches and activation."""

import math
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from company_brain.db import connect, migrate
from company_brain.embedding import (
    EmbeddingClient,
    IncompleteIndex,
    IndexMismatch,
    activate,
    active_index,
    check_index,
    index_id_for,
    load_embedding_models,
    register_index,
    store_chunk_vector,
)

MODELS = load_embedding_models()


class FakeOllama:
    def __init__(self, dimensions: int = 4) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.dimensions = dimensions

    def __call__(self, model: str, texts: list[str]) -> list[list[float]]:
        self.calls.append((model, list(texts)))
        return [[float(len(t) % 7 + 1)] + [1.0] * (self.dimensions - 1) for t in texts]


def test_three_candidate_models_configured() -> None:
    assert set(MODELS) == {"qwen3-embedding:0.6b", "bge-m3:latest", "embeddinggemma-2:270m"}


@pytest.mark.parametrize("name", sorted(MODELS))
def test_prefixes_come_from_models_toml(name: str) -> None:
    model = MODELS[name]
    fake = FakeOllama()
    client = EmbeddingClient(model, backend=fake)
    client.embed_query("What is the fee?")
    client.embed_documents(["Clause 1", "Clause 2"])
    (query_call, document_call) = fake.calls
    assert query_call == (name, [model.query_prefix + "What is the fee?"])
    assert document_call == (
        name,
        [model.document_prefix + "Clause 1", model.document_prefix + "Clause 2"],
    )


def test_gemma_and_qwen_prefixes_are_the_model_card_ones() -> None:
    assert MODELS["embeddinggemma-2:270m"].query_prefix == "task: search result | query: "
    assert MODELS["embeddinggemma-2:270m"].document_prefix == "title: none | text: "
    assert MODELS["qwen3-embedding:0.6b"].query_prefix.startswith("Instruct: ")
    assert MODELS["qwen3-embedding:0.6b"].document_prefix == ""
    assert MODELS["bge-m3:latest"].query_prefix == MODELS["bge-m3:latest"].document_prefix == ""


def test_batching_and_normalisation() -> None:
    fake = FakeOllama()
    client = EmbeddingClient(MODELS["bge-m3:latest"], backend=fake, batch_size=3)
    vectors = client.embed_documents([f"text {i}" for i in range(7)])
    assert [len(texts) for _, texts in fake.calls] == [3, 3, 1]
    assert len(vectors) == 7
    assert all(math.isclose(math.sqrt(sum(x * x for x in v)), 1.0) for v in vectors)


def test_backend_returning_wrong_count_fails() -> None:
    client = EmbeddingClient(MODELS["bge-m3:latest"], backend=lambda m, t: [[1.0, 0.0]])
    with pytest.raises(ValueError):
        client.embed_documents(["a", "b"])


def test_index_id_is_stable_and_distinct() -> None:
    a = index_id_for("bge-m3:latest", "digest1", 500)
    assert a == index_id_for("bge-m3:latest", "digest1", 500)
    assert a != index_id_for("bge-m3:latest", "digest1", 300)
    assert a != index_id_for("qwen3-embedding:0.6b", "digest1", 500)


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


INSERT_FILE = (
    "INSERT INTO index_files (index_id, doc_id, file_hash, status) VALUES (%s, %s, 'h', %s)"
)


def _document(conn: psycopg.Connection, doc_id: str, client: str = "acme") -> None:
    conn.execute(
        """INSERT INTO documents (doc_id, client, scope, doc_type, language, file_hash,
                                  availability, rel_path)
           VALUES (%s, %s, %s, 'report', 'en', 'h', 'available', %s)""",
        (doc_id, client, client, f"Clients/{client}/Reports/{doc_id}.pdf"),
    )


@pytest.mark.integration
def test_mismatched_model_or_dimension_fails(db: psycopg.Connection) -> None:
    model = MODELS["bge-m3:latest"]
    index_id = register_index(db, model, digest="d1", dimensions=4, chunk_budget=500)
    _document(db, "doc1")
    store_chunk_vector(db, index_id, model, "c1", "doc1", "text", "label", [0.5, 0.5, 0.5, 0.5])
    with pytest.raises(IndexMismatch):
        store_chunk_vector(db, index_id, model, "c2", "doc1", "text", "label", [1.0, 0.0, 0.0])
    with pytest.raises(IndexMismatch):
        store_chunk_vector(
            db, index_id, MODELS["qwen3-embedding:0.6b"], "c3", "doc1", "t", "l", [0.5] * 4
        )
    with pytest.raises(IndexMismatch):
        check_index(db, index_id, MODELS["qwen3-embedding:0.6b"], dimensions=4)
    with pytest.raises(IndexMismatch):
        store_chunk_vector(db, index_id, model, "c4", "doc1", "t", "l", [3.0, 0.0, 0.0, 0.0])


@pytest.mark.integration
def test_activation_requires_complete_index_and_is_atomic(db: psycopg.Connection) -> None:
    model = MODELS["bge-m3:latest"]
    first = register_index(db, model, digest="d1", dimensions=4, chunk_budget=500)
    second = register_index(db, model, digest="d1", dimensions=4, chunk_budget=300)
    _document(db, "doc1")
    _document(db, "doc2")
    for index_id in (first, second):
        db.execute(INSERT_FILE, (index_id, "doc1", "complete"))
    db.execute(INSERT_FILE, (first, "doc2", "complete"))
    db.commit()
    activate(db, first)
    assert active_index(db) == first
    with pytest.raises(IncompleteIndex):
        activate(db, second)
    assert active_index(db) == first
    db.execute(INSERT_FILE, (second, "doc2", "pending"))
    with pytest.raises(IncompleteIndex):
        activate(db, second)
    db.execute("UPDATE index_files SET status = 'complete' WHERE index_id = %s", (second,))
    db.commit()
    activate(db, second)
    assert active_index(db) == second
    assert db.execute("SELECT count(*) FROM index_meta WHERE active").fetchone() == (1,)


@pytest.mark.integration
def test_unavailable_and_tombstoned_documents_do_not_block_activation(
    db: psycopg.Connection,
) -> None:
    index_id = register_index(
        db, MODELS["bge-m3:latest"], digest="d1", dimensions=4, chunk_budget=500
    )
    _document(db, "doc1")
    _document(db, "gone", client="gone")
    _document(db, "offline")
    db.execute("UPDATE documents SET availability = 'unavailable' WHERE doc_id = 'offline'")
    db.execute("INSERT INTO tombstones (client) VALUES ('gone')")
    db.execute(INSERT_FILE, (index_id, "doc1", "complete"))
    db.commit()
    activate(db, index_id)
    assert active_index(db) == index_id
