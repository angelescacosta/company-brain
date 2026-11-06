"""Incremental, crash-safe ingestion (T20).

One run: scan the root (T15), record unavailable and listed files (T16), then
for every reachable file and every index, convert (T17), chunk with that
index's budget (T18), embed and store, in one transaction per file and index,
committed before the next. A file whose content is unchanged in an index is
skipped there, so a re-run embeds nothing and a new index fills completely.

Deletions (and rename detection, which retires the old path) happen only after
a scan with no errors and a present root, and apply to every index.
Tombstoned clients are never scanned. Transient embedding errors are retried;
a file that still fails is marked failed in that index and the run goes on.
The run's row in `ingest_runs` records counts and "indexed up to".
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from company_brain import scanner
from company_brain.availability import apply_statuses, check_availability
from company_brain.chunkers import Chunk, chunk_document
from company_brain.content_guard import VerifiedBytes
from company_brain.convert import Converted, Docling, convert
from company_brain.embedding import UNIT_TOLERANCE, EmbeddingModel, IndexMismatch, check_index

TRANSIENT = (ConnectionError, TimeoutError, OSError)
FOLDER_ROLES = {"Transcripts": "call", "Contracts": "contract"}


@dataclass(frozen=True)
class Target:
    index_id: str
    budget: int
    model: EmbeddingModel
    embed: Callable[[list[str]], list[list[float]]]  # document texts -> unit vectors


@dataclass
class RunResult:
    run_id: str
    state: str = "running"
    added: int = 0
    changed: int = 0
    renamed: int = 0
    removed: int = 0
    failed: int = 0
    deletions_skipped: bool = False
    stopped: bool = False  # asked to stop between files (T20U)


Metadata = Callable[[scanner.ScannedFile], Mapping[str, str]]


def default_metadata(item: scanner.ScannedFile) -> dict[str, str]:
    """Role from the folder; the product passes the manifest's role and language instead."""
    return {"role": FOLDER_ROLES.get(item.folder or "", "document"), "language": "mixed"}


def indexed(conn: psycopg.Connection, index_id: str) -> dict[str, str]:
    """Relative path -> file hash of every complete file in an index (with chunks)."""
    rows = conn.execute(
        """SELECT d.rel_path, f.file_hash FROM brain.index_files f
           JOIN brain.documents d USING (doc_id)
           WHERE f.index_id = %s AND f.status = 'complete'
             AND EXISTS (SELECT 1 FROM brain.chunks c
                         WHERE c.index_id = f.index_id AND c.doc_id = f.doc_id)""",
        (index_id,),
    ).fetchall()
    return {path: digest for path, digest in rows}


def _insert_chunk(
    conn: psycopg.Connection,
    chunk_id: str,
    index_id: str,
    doc_id: str,
    chunk: Chunk,
    vector: Sequence[float],
) -> None:
    conn.execute(
        """INSERT INTO brain.chunks (chunk_id, index_id, doc_id, text, label, parent_id,
                                     provenance, source_spans, embedding)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            chunk_id,
            index_id,
            doc_id,
            chunk.text,
            chunk.label,
            chunk.parent,
            json.dumps({"kind": chunk.kind, "flags": sorted(chunk.flags)}),
            json.dumps(list(chunk.spans), ensure_ascii=False),
            "[" + ",".join(map(repr, vector)) + "]",
        ),
    )


def _embed(
    target: Target, texts: list[str], retries: int, sleep: Callable[[float], None]
) -> list[list[float]]:
    for attempt in range(retries + 1):
        try:
            vectors = target.embed(texts)
            break
        except TRANSIENT:
            if attempt == retries:
                raise
            sleep(2**attempt)
    if len(vectors) != len(texts):
        raise IndexMismatch(f"expected {len(texts)} vectors, got {len(vectors)}")
    for vector in vectors:
        if abs(math.sqrt(math.fsum(x * x for x in vector)) - 1) > UNIT_TOLERANCE:
            raise IndexMismatch("vector is not unit length")
    return vectors


def _doc_type(role: str, relative: str) -> tuple[str, str | None]:
    lower = relative.lower()
    status = "draft" if ("draft" in lower or "borrador" in lower) else None
    if "signed" in lower or "firmado" in lower:
        status = "signed"
    return role, status


def _set_failed(conn: psycopg.Connection, index_id: str, doc_id: str, file_hash: str) -> None:
    conn.execute(
        """INSERT INTO brain.index_files (index_id, doc_id, file_hash, status)
           VALUES (%s, %s, %s, 'failed')
           ON CONFLICT (index_id, doc_id) DO UPDATE
           SET status = 'failed', updated_at = now()
           WHERE brain.index_files.status <> 'complete'""",
        (index_id, doc_id, file_hash),
    )


def ingest(
    conn: psycopg.Connection,
    root: Path,
    targets: Sequence[Target],
    read: Callable[[Path], VerifiedBytes],
    *,
    trigger: str = "manual",
    metadata: Metadata = default_metadata,
    scan_fn: Callable[..., scanner.ScanReport] = scanner.scan,
    is_listed: Callable[[Path], bool] | None = None,
    stat: Callable[[str], os.stat_result] = os.stat,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = 3,
    docling: Docling | None = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> RunResult:
    result = RunResult(run_id=uuid.uuid4().hex)
    docling = docling or Docling()  # one converter per run; its models load on first use
    conn.commit()
    conn.execute(
        "INSERT INTO brain.ingest_runs (run_id, trigger, state) VALUES (%s, %s, 'running')",
        (result.run_id, trigger),
    )
    conn.commit()
    tombstones = {row[0] for row in conn.execute("SELECT client FROM brain.tombstones")}
    try:
        report = scan_fn(root, tombstones=tombstones, is_listed=is_listed)
    except scanner.SourceUnavailable:
        return _finish(conn, result, "failed")
    available, statuses = check_availability(report, stat)
    apply_statuses(conn, statuses)
    conn.commit()
    complete = report.complete

    documents = {
        path: (doc_id, digest)
        for doc_id, path, digest in conn.execute(
            "SELECT doc_id, rel_path, file_hash FROM brain.documents"
        )
    }
    present = {item.relative.as_posix() for item in report.files}
    converted: dict[str, Converted] = {}
    chunked: dict[tuple[str, int], list[Chunk]] = {}

    for item in available:
        if should_stop():  # a file boundary: every file so far is fully old or fully new
            result.stopped = True
            break
        relative = item.relative.as_posix()
        if item.client in tombstones:
            continue
        verified = read(item.path)
        digest = verified.sha256
        info = dict(metadata(item))
        role, status = _doc_type(info.get("role", "document"), relative)
        if relative in documents:
            doc_id, old_digest = documents[relative]
            if old_digest != digest:
                result.changed += 1
        else:
            moved = (
                [
                    (path, doc)
                    for path, (doc, old) in documents.items()
                    if old == digest and path not in present
                ]
                if complete
                else []
            )
            if moved:
                old_path, doc_id = moved[0]
                del documents[old_path]
                result.renamed += 1
            else:
                doc_id = hashlib.sha256(f"{relative}|{digest}".encode()).hexdigest()[:20]
                result.added += 1
        conn.execute(
            """INSERT INTO brain.documents (doc_id, client, scope, doc_type, language, status,
                                            file_hash, availability, rel_path)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'available', %s)
               ON CONFLICT (doc_id) DO UPDATE SET client = EXCLUDED.client,
                   scope = EXCLUDED.scope, doc_type = EXCLUDED.doc_type,
                   language = EXCLUDED.language, status = EXCLUDED.status,
                   file_hash = EXCLUDED.file_hash, availability = 'available',
                   rel_path = EXCLUDED.rel_path, updated_at = now()""",
            (
                doc_id,
                item.client or item.scope,
                item.scope,
                role,
                info.get("language", "mixed"),
                status,
                digest,
                relative,
            ),
        )
        conn.commit()
        documents[relative] = (doc_id, digest)
        file_failed = False
        for target in targets:
            state = conn.execute(
                "SELECT file_hash, status FROM brain.index_files WHERE index_id = %s "
                "AND doc_id = %s",
                (target.index_id, doc_id),
            ).fetchone()
            if state == (digest, "complete"):
                continue
            if relative not in converted:
                converted[relative] = convert(verified, docling)
            done = converted[relative]
            if done.status != "converted":
                _set_failed(conn, target.index_id, doc_id, digest)
                conn.commit()
                file_failed = True
                continue
            key = (relative, target.budget)
            if key not in chunked:
                source = item.client or item.scope
                chunked[key] = chunk_document(done, role, target.budget, source)
            chunks = chunked[key]
            try:
                vectors = _embed(target, [c.full() for c in chunks], retries, sleep)
            except TRANSIENT:
                _set_failed(conn, target.index_id, doc_id, digest)
                conn.commit()
                file_failed = True
                continue
            check_index(conn, target.index_id, target.model, len(vectors[0]))
            with conn.transaction():
                conn.execute(
                    "DELETE FROM brain.chunks WHERE index_id = %s AND doc_id = %s",
                    (target.index_id, doc_id),
                )
                for number, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                    _insert_chunk(
                        conn,
                        f"{target.index_id}:{doc_id}:{number}",
                        target.index_id,
                        doc_id,
                        chunk,
                        vector,
                    )
                conn.execute(
                    """INSERT INTO brain.index_files (index_id, doc_id, file_hash, status)
                       VALUES (%s, %s, %s, 'complete')
                       ON CONFLICT (index_id, doc_id) DO UPDATE
                       SET file_hash = EXCLUDED.file_hash, status = 'complete',
                           updated_at = now()""",
                    (target.index_id, doc_id, digest),
                )
            conn.commit()
        result.failed += file_failed

    if complete and not result.stopped:
        gone = [doc for path, (doc, _d) in documents.items() if path not in present]
        for doc_id in gone:
            conn.execute(
                "DELETE FROM brain.documents WHERE doc_id = %s AND client NOT IN "
                "(SELECT client FROM brain.tombstones)",
                (doc_id,),
            )
        result.removed = len(gone)
        conn.commit()
    else:
        result.deletions_skipped = True
    return _finish(conn, result, "failed" if result.stopped else "completed")


def _finish(conn: psycopg.Connection, result: RunResult, state: str) -> RunResult:
    result.state = state
    conn.execute(
        """UPDATE brain.ingest_runs SET state = %s, ended_at = now(), added = %s, changed = %s,
               removed = %s, failed = %s,
               indexed_up_to = CASE WHEN %s = 'completed' THEN started_at END
           WHERE run_id = %s""",
        (state, result.added, result.changed, result.removed, result.failed, state, result.run_id),
    )
    conn.commit()
    return result


def file_metadata(manifests: Mapping[str, Mapping[str, Any]]) -> Metadata:
    """Role and language from the manifests (test mode), falling back to the folder."""

    def lookup(item: scanner.ScannedFile) -> Mapping[str, str]:
        entry = manifests.get(item.relative.as_posix())
        if entry is None:
            return default_metadata(item)
        language = entry.get("language", "mixed")
        return {
            "role": str(entry.get("role", "document")),
            "language": language if language in {"en", "es"} else "mixed",
        }

    return lookup
