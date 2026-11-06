"""T21 PBT: client scope and rank fusion.

Generated multi-client corpora and queries: every result belongs to the
requested client or to the shared methodology scope, and a call without a
scope, or with one that does not match the chat, raises (client leakage is a
release blocker). Fusion: deterministic, ties broken by stable ID, and a chunk
ranked first in both lists is first overall.
"""

from collections.abc import Iterator

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from ingest_helpers import DIMENSIONS, FakeEmbedder, fresh_db

from company_brain import retrieval
from company_brain.embedding import load_embedding_models, register_index

MODEL = load_embedding_models()["bge-m3:latest"]
WORDS = ["fee", "Leeds", "pilot", "honorarios", "plazo", "clause", "4", "Dana", "Rocío", "report"]
ids = st.lists(st.text("abcdefgh", min_size=1, max_size=4), unique=True, max_size=25)


@given(ids, ids)
def test_fusion_is_deterministic_with_stable_ties(vector: list[str], keyword: list[str]) -> None:
    fused = retrieval.fuse(vector, keyword)
    assert fused == retrieval.fuse(list(vector), list(keyword))
    assert sorted(fused) == sorted(set(vector) | set(keyword))
    scores = retrieval.rrf_scores(vector, keyword)
    for a, b in zip(fused, fused[1:], strict=False):
        assert scores[a] > scores[b] or (scores[a] == scores[b] and a < b)
    if vector and keyword and vector[0] == keyword[0]:
        assert fused[0] == vector[0]


@pytest.fixture(scope="module")
def db() -> Iterator[psycopg.Connection]:
    conn = fresh_db()
    yield conn
    conn.close()


corpus = st.lists(
    st.tuples(
        st.sampled_from(["A", "B", "C", retrieval.SHARED]),
        st.lists(st.sampled_from(WORDS), min_size=2, max_size=12).map(" ".join),
    ),
    min_size=1,
    max_size=20,
)


@pytest.mark.integration
@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    corpus,
    st.sampled_from(["A", "B", "C"]),
    st.lists(st.sampled_from(WORDS), min_size=1, max_size=5),
)
def test_results_never_leave_the_client(
    db: psycopg.Connection, docs: list[tuple[str, str]], client: str, words: list[str]
) -> None:
    db.execute("DELETE FROM brain.index_meta")
    db.execute("DELETE FROM brain.documents")
    index_id = register_index(db, MODEL, digest="d", dimensions=DIMENSIONS, chunk_budget=500)
    db.execute(
        "UPDATE brain.index_meta SET active = true, complete = true WHERE index_id = %s",
        (index_id,),
    )
    embed = FakeEmbedder()
    for number, (scope, text) in enumerate(docs):
        doc_id = f"doc{number}"
        db.execute(
            """INSERT INTO brain.documents (doc_id, client, scope, doc_type, language, file_hash,
                                            availability, rel_path)
               VALUES (%s, %s, %s, 'report', 'en', 'h', 'available', %s)""",
            (doc_id, scope, scope, f"x/{doc_id}.vtt"),
        )
        vector = embed([text])[0]
        db.execute(
            """INSERT INTO brain.chunks (chunk_id, index_id, doc_id, text, label, embedding)
               VALUES (%s, %s, %s, %s, 'label', %s)""",
            (f"c{number}", index_id, doc_id, text, "[" + ",".join(map(repr, vector)) + "]"),
        )
    db.commit()
    query = " ".join(words)
    hits = retrieval.retrieve(db, embed([query])[0], query, scope=client, chat_scope=client)
    assert all(hit.scope in {client, retrieval.SHARED} for hit in hits)
    shared = [n for n, (s, _t) in enumerate(docs) if s == retrieval.SHARED]
    if shared and len(docs) <= 20:
        assert {f"c{n}" for n in shared} <= {h.chunk_id for h in hits}  # AC2: always eligible
    with pytest.raises(retrieval.ScopeError):
        retrieval.retrieve(db, embed([query])[0], query, scope="", chat_scope=client)
    with pytest.raises(retrieval.ScopeError):
        other = "B" if client != "B" else "C"
        retrieval.retrieve(db, embed([query])[0], query, scope=other, chat_scope=client)
