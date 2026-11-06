"""Embedding client and index registry (T19).

Each embedding model gets its own query and document prefixes from
`config/models.toml` (taken from the official model cards). Every
configuration (model, model digest and chunk budget) has its own index ID;
exactly one index is active, and only a complete index can be activated.
"""

from __future__ import annotations

import hashlib
import math
import os
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg

MODELS_TOML = Path(__file__).resolve().parents[2] / "config" / "models.toml"
OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_URL_ENV = "COMPANY_BRAIN_OLLAMA_URL"  # set by the service job; tests point it at their copy


def ollama_url() -> str:
    """The local Ollama address (the real one unless the service job says otherwise)."""
    return os.environ.get(OLLAMA_URL_ENV, OLLAMA_HOST)


UNIT_TOLERANCE = 1e-4

Backend = Callable[[str, list[str]], Sequence[Sequence[float]]]


class IndexMismatch(ValueError):
    """A vector or query does not fit the index's model or dimension."""


class IncompleteIndex(RuntimeError):
    """The index does not yet cover every available document."""


def normalise(vector: Sequence[float]) -> list[float]:
    """Scale to unit length; refuse empty, zero or non-finite vectors."""
    if not vector:
        raise ValueError("empty vector")
    if not all(math.isfinite(x) for x in vector):
        raise ValueError("vector has a non-finite value")
    scale = max(abs(x) for x in vector)
    if scale == 0:
        raise ValueError("zero vector")
    scaled = [x / scale for x in vector]
    norm = math.sqrt(math.fsum(x * x for x in scaled))
    return [x / norm for x in scaled]


@dataclass(frozen=True)
class EmbeddingModel:
    name: str
    query_prefix: str
    document_prefix: str
    digest: str


def load_embedding_models(path: Path = MODELS_TOML) -> dict[str, EmbeddingModel]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        name: EmbeddingModel(
            name=name,
            query_prefix=entry["query_prefix"],
            document_prefix=entry["document_prefix"],
            digest=data["ollama"][name]["digest"],
        )
        for name, entry in data["embedding"].items()
    }


def ollama_backend(model: str, texts: list[str]) -> Sequence[Sequence[float]]:
    import ollama

    client = ollama.Client(host=ollama_url())
    return client.embed(model=model, input=texts).embeddings


class EmbeddingClient:
    def __init__(
        self, model: EmbeddingModel, backend: Backend = ollama_backend, batch_size: int = 32
    ) -> None:
        self.model = model
        self._backend = backend
        self._batch_size = batch_size

    def _embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            result = self._backend(self.model.name, batch)
            if len(result) != len(batch):
                raise ValueError(f"expected {len(batch)} vectors, got {len(result)}")
            vectors.extend(normalise(v) for v in result)
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self.model.query_prefix + text])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed([self.model.document_prefix + t for t in texts])


def index_id_for(model: str, digest: str, chunk_budget: int) -> str:
    return hashlib.sha256(f"{model}|{digest}|{chunk_budget}".encode()).hexdigest()[:16]


def register_index(
    conn: psycopg.Connection,
    model: EmbeddingModel,
    digest: str,
    dimensions: int,
    chunk_budget: int,
) -> str:
    index_id = index_id_for(model.name, digest, chunk_budget)
    conn.execute(
        """INSERT INTO brain.index_meta (index_id, model, revision, dimensions, instruction,
                                         chunk_budget)
           VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (index_id) DO NOTHING""",
        (index_id, model.name, digest, dimensions, model.query_prefix, chunk_budget),
    )
    return index_id


def check_index(
    conn: psycopg.Connection, index_id: str, model: EmbeddingModel, dimensions: int
) -> None:
    """Fail clearly if the model or dimension does not match the index."""
    row = conn.execute(
        "SELECT model, dimensions FROM brain.index_meta WHERE index_id = %s", (index_id,)
    ).fetchone()
    if row is None:
        raise IndexMismatch(f"index {index_id} does not exist")
    if row[0] != model.name:
        raise IndexMismatch(f"index {index_id} uses {row[0]}, not {model.name}")
    if row[1] != dimensions:
        raise IndexMismatch(f"index {index_id} has {row[1]} dimensions, not {dimensions}")


def store_chunk_vector(
    conn: psycopg.Connection,
    index_id: str,
    model: EmbeddingModel,
    chunk_id: str,
    doc_id: str,
    text: str,
    label: str,
    vector: Sequence[float],
) -> None:
    check_index(conn, index_id, model, len(vector))
    if abs(math.sqrt(math.fsum(x * x for x in vector)) - 1) > UNIT_TOLERANCE:
        raise IndexMismatch("vector is not unit length")
    conn.execute(
        """INSERT INTO brain.chunks (chunk_id, index_id, doc_id, text, label, embedding)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (chunk_id, index_id, doc_id, text, label, "[" + ",".join(map(repr, vector)) + "]"),
    )


def active_index(conn: psycopg.Connection) -> str | None:
    row = conn.execute("SELECT index_id FROM brain.index_meta WHERE active").fetchone()
    return row[0] if row else None


def activate(conn: psycopg.Connection, index_id: str) -> None:
    """Make `index_id` the only active index, in one transaction, if it is complete."""
    with conn.transaction():
        missing = conn.execute(
            """SELECT count(*) FROM brain.documents d
               WHERE d.availability = 'available'
                 AND d.client NOT IN (SELECT client FROM brain.tombstones)
                 AND NOT EXISTS (
                     SELECT 1 FROM brain.index_files f
                     WHERE f.index_id = %s AND f.doc_id = d.doc_id AND f.status = 'complete')""",
            (index_id,),
        ).fetchone()
        if missing is None or missing[0]:
            count = missing[0] if missing else "?"
            raise IncompleteIndex(f"index {index_id} is missing {count} available document(s)")
        conn.execute("UPDATE brain.index_meta SET active = false WHERE active")
        conn.execute(
            "UPDATE brain.index_meta SET active = true, complete = true WHERE index_id = %s",
            (index_id,),
        )
