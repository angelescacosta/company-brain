"""Index the documents in the Company Brain folder (T20), as the update command runs it (T20U).

Reads only files listed in the test manifests (test mode, T9), through the
content guard, into the assistant's database; registers the index for the
model and budget if needed, ingests, and activates it when it is complete.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from company_brain import ingest
from company_brain.content_guard import GuardedReader, Manifest
from company_brain.db import connect
from company_brain.drive import load_sources
from company_brain.embedding import (
    EmbeddingClient,
    IncompleteIndex,
    load_embedding_models,
    register_index,
)
from company_brain.embedding import activate as activate_index
from company_brain.freeze import frozen_retrieval
from company_brain.settings import Settings

REPO = Path(__file__).resolve().parents[2]


def index_documents(
    settings: Settings | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    model_name: str | None = None,
    budget: int | None = None,
    trigger: str = "manual",
    activate: bool = True,
) -> ingest.RunResult:
    settings = settings or Settings.from_env()
    # T37: the frozen configuration decides the index; before the freeze, the original default
    frozen = frozen_retrieval() or ("bge-m3:latest", 500)
    model_name = model_name or frozen[0]
    budget = budget or frozen[1]
    root = settings.root or load_sources(REPO / "config" / "sources.toml")["root"]
    manifests = {}
    for name in ("fictional.json", "public.json"):
        for entry in json.loads((REPO / "data" / "manifests" / name).read_text())["files"]:
            manifests[entry["path"]] = entry
    reader = GuardedReader(root, Manifest.load(REPO / "data" / "manifests"))
    model = load_embedding_models()[model_name]
    digest = tomllib.loads((REPO / "config" / "models.toml").read_text())["ollama"][model_name][
        "digest"
    ]
    client = EmbeddingClient(model)
    dimensions = len(client.embed_query("dimension check"))
    with connect(settings) as conn:
        index_id = register_index(conn, model, digest, dimensions, budget)
        conn.commit()
        target = ingest.Target(index_id, budget, model, client.embed_documents)
        result = ingest.ingest(
            conn,
            root,
            [target],
            lambda p: reader.read(p),
            trigger=trigger,
            is_listed=reader.is_listed,
            metadata=ingest.file_metadata(manifests),
            should_stop=should_stop,
        )
        finish(conn, index_id, result, activate)
    return result


def finish(conn: Any, index_id: str, result: ingest.RunResult, activate: bool) -> None:
    """Make a complete index the assistant's own; a tuning candidate (T35) never is."""
    if result.state != "completed" or not activate:
        return
    try:
        activate_index(conn, index_id)
        conn.commit()
    except IncompleteIndex:
        conn.rollback()


def listed_files(settings: Settings | None = None) -> list[tuple[str, str, int | None, str]]:
    """Files that were reported but not read: (status, path, size, reason)."""
    with connect(settings or Settings.from_env()) as conn:
        rows = conn.execute(
            "SELECT status, rel_path, size, reason FROM brain.file_status ORDER BY status, rel_path"
        ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]
