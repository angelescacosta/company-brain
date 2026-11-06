"""Hybrid retrieval with a mandatory client scope (T21).

Exact cosine search (top 20, no approximate index) and keyword search over the
`english`, `spanish` and `simple` text configurations (top 20), fused with
reciprocal rank fusion, on the active index. Every call names one client scope,
which must equal the chat's bound scope (T29); the shared methodology scope is
always added. Parent expansion stays inside the same scopes.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg

SHARED = "shared"
TOP_K = 20
RRF_K = 60
_WORD = re.compile(r"\w+", re.UNICODE)


class ScopeError(ValueError):
    """No client scope, or a scope that is not the chat's."""


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: str
    scope: str
    client: str
    rel_path: str
    text: str
    label: str
    parent_id: str | None
    spans: tuple[dict[str, Any], ...]
    score: float


def rrf_scores(*rankings: Sequence[str]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (RRF_K + rank)
    return scores


def fuse(*rankings: Sequence[str]) -> list[str]:
    """Reciprocal rank fusion; equal scores are ordered by ID, so the result is stable."""
    scores = rrf_scores(*rankings)
    return sorted(scores, key=lambda item: (-scores[item], item))


def scopes_for(scope: str, chat_scope: str) -> list[str]:
    if not scope or not chat_scope:
        raise ScopeError("every search needs the chat's client scope")
    if scope != chat_scope:
        raise ScopeError("the search scope does not match this chat's client")
    if scope == SHARED:
        raise ScopeError("the shared methodology is not a client")
    return [scope, SHARED]


def _keyword_query(text: str) -> str:
    """Words joined with OR; only letters and digits reach to_tsquery, so no syntax gets in."""
    words = list(dict.fromkeys(w.lower() for w in _WORD.findall(text)))
    return " | ".join(words)


def _active(conn: psycopg.Connection, index_id: str | None) -> str:
    if index_id is not None:
        return index_id
    row = conn.execute("SELECT index_id FROM brain.index_meta WHERE active").fetchone()
    if row is None:
        raise LookupError("no active index")
    return str(row[0])


_COLUMNS = (
    "c.chunk_id, c.doc_id, d.scope, d.client, d.rel_path, c.text, c.label, c.parent_id, "
    "c.source_spans"
)


def retrieve(
    conn: psycopg.Connection,
    query_vector: Sequence[float],
    query_text: str,
    scope: str,
    chat_scope: str,
    index_id: str | None = None,
    k: int = TOP_K,
) -> list[Hit]:
    scopes = scopes_for(scope, chat_scope)
    index_id = _active(conn, index_id)
    vector = "[" + ",".join(map(repr, query_vector)) + "]"
    rows: dict[str, tuple[Any, ...]] = {}
    by_vector = conn.execute(
        f"""SELECT {_COLUMNS} FROM brain.chunks c JOIN brain.documents d USING (doc_id)
            WHERE c.index_id = %s AND d.scope = ANY(%s) AND c.embedding IS NOT NULL
            ORDER BY c.embedding <=> %s::vector, c.chunk_id LIMIT %s""",  # noqa: S608
        (index_id, scopes, vector, k),
    ).fetchall()
    keyword: list[tuple[Any, ...]] = []
    terms = _keyword_query(query_text)
    if terms:
        keyword = conn.execute(
            f"""WITH q AS (SELECT to_tsquery('english', %(t)s) AS en,
                                  to_tsquery('spanish', %(t)s) AS es,
                                  to_tsquery('simple', %(t)s) AS si)
                SELECT {_COLUMNS},
                       greatest(ts_rank_cd(c.tsv_en, q.en), ts_rank_cd(c.tsv_es, q.es),
                                ts_rank_cd(c.tsv_simple, q.si)) AS rank
                FROM brain.chunks c JOIN brain.documents d USING (doc_id), q
                WHERE c.index_id = %(i)s AND d.scope = ANY(%(s)s)
                  AND (c.tsv_en @@ q.en OR c.tsv_es @@ q.es OR c.tsv_simple @@ q.si)
                ORDER BY rank DESC, c.chunk_id LIMIT %(k)s""",  # noqa: S608
            {"t": terms, "i": index_id, "s": scopes, "k": k},
        ).fetchall()
    for row in [*by_vector, *keyword]:
        rows[row[0]] = row[:9]
    scores = rrf_scores([r[0] for r in by_vector], [r[0] for r in keyword])
    ranked = sorted(scores, key=lambda item: (-scores[item], item))
    hits = [_hit(rows[chunk_id], scores[chunk_id]) for chunk_id in ranked]
    if any(hit.scope not in scopes for hit in hits):  # defence in depth
        raise ScopeError("a result came from outside the chat's scope")
    return hits


def _hit(row: tuple[Any, ...], score: float) -> Hit:
    spans = row[8] if isinstance(row[8], list) else json.loads(row[8] or "[]")
    return Hit(
        chunk_id=row[0],
        doc_id=row[1],
        scope=row[2],
        client=row[3],
        rel_path=row[4],
        text=row[5],
        label=row[6],
        parent_id=row[7],
        spans=tuple(spans),
        score=score,
    )


def siblings(
    conn: psycopg.Connection, hit: Hit, scope: str, chat_scope: str, index_id: str | None = None
) -> list[Hit]:
    """Other chunks with the same parent (a split clause or section), in the same scopes."""
    scopes = scopes_for(scope, chat_scope)
    if hit.parent_id is None:
        return []
    rows = conn.execute(
        f"""SELECT {_COLUMNS} FROM brain.chunks c JOIN brain.documents d USING (doc_id)
            WHERE c.index_id = %s AND c.parent_id = %s AND d.scope = ANY(%s)
              AND c.chunk_id <> %s
            ORDER BY c.chunk_id""",  # noqa: S608
        (_active(conn, index_id), hit.parent_id, scopes, hit.chunk_id),
    ).fetchall()
    return [_hit(row, 0.0) for row in rows]


def counterpart_map(conn: psycopg.Connection, scope: str, chat_scope: str) -> dict[str, set[str]]:
    """Draft and signed versions of an agreement within one scope, each pointing to the other."""
    scopes = scopes_for(scope, chat_scope)
    rows = conn.execute(
        """SELECT doc_id, status FROM brain.documents
           WHERE scope = ANY(%s) AND status IN ('draft', 'signed')
             AND doc_type IN ('contract', 'draft')""",
        (scopes,),
    ).fetchall()
    drafts = {doc for doc, status in rows if status == "draft"}
    signed = {doc for doc, status in rows if status == "signed"}
    pairs: dict[str, set[str]] = {doc: set(signed) for doc in drafts}
    pairs.update({doc: set(drafts) for doc in signed})
    return pairs
