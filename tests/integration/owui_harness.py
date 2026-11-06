"""Shared setup for tests through Open WebUI: test services, a small index, an admin, the Pipe."""

import json
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest

from company_brain import corpus, ingest, pipe_install
from company_brain.content_guard import GuardedReader, Manifest
from company_brain.embedding import EmbeddingClient, load_embedding_models, register_index
from company_brain.services import PG_BIN, Gate, Launchd, define, jobs_dir, listening, real_gate
from company_brain.services import start as start_services
from company_brain.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingest_helpers import fresh_db  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EMAIL = "owner@example.test"


@dataclass
class Running:
    url: str
    token: str
    password: str


def call(
    url: str,
    path: str,
    payload: dict | None = None,
    token: str = "",  # type: ignore[type-arg]
    timeout: float = 600,
) -> dict:  # type: ignore[type-arg]
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(  # noqa: S310 - the test Open WebUI on 127.0.0.1
        url + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers=headers,
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read())  # type: ignore[no-any-return]
    except urllib.error.HTTPError as error:
        raise AssertionError(f"{path}: {error.code} {error.read()[:500]!r}") from error


def wait_ready(url: str, seconds: float = 120) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            call(url, "/health", timeout=5)
            return
        except (AssertionError, urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(1)
    raise AssertionError("Open WebUI did not become ready")


def init_cluster(settings: Settings) -> None:
    if not (settings.pg_data / "PG_VERSION").exists():
        subprocess.run(
            [
                str(PG_BIN / "initdb"),
                "-D",
                str(settings.pg_data),
                "-A",
                "trust",
                "--no-instructions",
            ],
            capture_output=True,
            check=True,
        )


def index_client(settings: Settings, monkeypatch: pytest.MonkeyPatch, client: str) -> None:
    """Index one fictional client's transcripts, Word files and spreadsheets (test database)."""
    monkeypatch.setenv("COMPANY_BRAIN_OLLAMA_URL", f"http://127.0.0.1:{settings.ollama_port}")
    conn = fresh_db()
    root = settings.root  # the assistant service reads the same test root
    assert root is not None
    corpus.build(root / corpus.PREFIX)
    reader = GuardedReader(root, Manifest.load(REPO / "data" / "manifests"))
    model = load_embedding_models()["bge-m3:latest"]
    embedder = EmbeddingClient(model)
    index_id = register_index(conn, model, "test", len(embedder.embed_query("x")), 500)
    conn.commit()
    wanted = lambda p: client in str(p) and p.suffix.lower() in {".vtt", ".docx", ".xlsx"}  # noqa: E731
    ingest.ingest(
        conn,
        root,
        [ingest.Target(index_id, 500, model, embedder.embed_documents)],
        lambda p: reader.read(p),
        is_listed=wanted,
    )
    conn.execute(
        "UPDATE brain.index_meta SET active = true, complete = true WHERE index_id = %s",
        (index_id,),
    )
    conn.commit()
    conn.close()


def bring_up(settings: Settings, monkeypatch: pytest.MonkeyPatch, client: str) -> Running:
    """Start the test services, index a client, create the admin, install the Pipe."""
    init_cluster(settings)
    # Each test starts with a fresh test copy of Open WebUI's data (accounts, chats, the Pipe).
    shutil.rmtree(settings.data_dir / "openwebui", ignore_errors=True)
    launchd = Launchd(jobs_dir(settings))
    gate = Gate(
        static_problems=real_gate().static_problems, online=lambda: False, probes=lambda _h: []
    )
    assert start_services(settings, gate, launchd, timeout=180).ok
    url = f"http://127.0.0.1:{settings.openwebui_port}"
    wait_ready(url)
    index_client(settings, monkeypatch, client)
    password = secrets.token_urlsafe(16)  # a throwaway account in the test copy
    call(url, "/api/v1/auths/signup", {"name": "Test Owner", "email": EMAIL, "password": password})
    webui = next(s for s in define(settings) if s.name == "openwebui")
    launchd.stop(webui.label)
    deadline = time.monotonic() + 30
    while listening(webui.port) and time.monotonic() < deadline:
        time.sleep(0.5)
    pipe_install.install(settings)
    launchd.start(webui)
    wait_ready(url)
    token = call(url, "/api/v1/auths/signin", {"email": EMAIL, "password": password})["token"]
    return Running(url, str(token), password)
