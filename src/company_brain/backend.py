"""The assistant's local service (T29): chat bindings and the modes.

Listens on 127.0.0.1 only (T4) and answers the Open WebUI Pipe. Every request
carries a token; every chat is bound to one client scope when it starts, and
any later request whose chat or client does not match the stored binding is
refused. Each reply has passed the output gate (T25).

Endpoints (JSON): GET /health; POST /chat/state {chat_id}; POST /chats
{chat_id, client}; POST /ask {chat_id, client, mode, message, history}.
Run: `uv run python -m company_brain.backend` (started by Start AI).
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol

import psycopg

SHARED = "shared"
TOKEN_FILE = "backend.token"  # noqa: S105 - a file name, not a secret
MAX_BODY = 1 << 20  # 1 MB: far above a question with its chat history (T46 F-01)
MODES_BY_PROFILE = {"lite": ("answer", "summarise"), "full": ("answer", "summarise", "draft")}


class BindingError(PermissionError):
    """The chat is not bound to this client (or not bound at all)."""


class Store(Protocol):
    def get(self, chat_id: str) -> str | None: ...
    def put(self, chat_id: str, scope: str) -> None: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, chat_id: str) -> str | None:
        return self._data.get(chat_id)

    def put(self, chat_id: str, scope: str) -> None:
        self._data[chat_id] = scope


class DatabaseStore:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()

    def get(self, chat_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT scope FROM brain.chat_bindings WHERE chat_id = %s", (chat_id,)
            ).fetchone()
            self._conn.commit()
        return str(row[0]) if row else None

    def put(self, chat_id: str, scope: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO brain.chat_bindings (chat_id, scope) VALUES (%s, %s) "
                "ON CONFLICT (chat_id) DO NOTHING",
                (chat_id, scope),
            )
            self._conn.commit()


class Bindings:
    def __init__(self, store: Store) -> None:
        self._store = store

    def bind(self, chat_id: str, scope: str) -> None:
        if not chat_id or not scope or scope == SHARED:
            raise BindingError("a chat needs an ID and a client")
        current = self._store.get(chat_id)
        if current is None:
            self._store.put(chat_id, scope)
        elif current != scope:
            raise BindingError("this chat already belongs to another client")

    def check(self, chat_id: str, scope: str) -> None:
        if not chat_id or not scope:
            raise BindingError("a chat needs an ID and a client")
        if self._store.get(chat_id) != scope:
            raise BindingError("this chat does not belong to that client")

    def client_of(self, chat_id: str) -> str | None:
        return self._store.get(chat_id) if chat_id else None


def display_name(scope: str) -> str:
    if scope == "public":
        return "Public test documents"
    if scope.startswith("fictional:"):
        return f"{scope.split(':', 1)[1]} (fictional)"
    return scope


class Downloads:
    """Exported files the browser may fetch by a long random ID (only from the export folder)."""

    def __init__(self, folder: Path) -> None:
        self.folder = Path(os.path.realpath(folder))
        self._files: dict[str, Path] = {}
        self._lock = threading.Lock()

    def add(self, path: Path) -> str:
        real = Path(os.path.realpath(path))
        if self.folder not in real.parents:
            raise ValueError("only files in the export folder can be downloaded")
        key = secrets.token_urlsafe(24)
        with self._lock:
            self._files[key] = real
        return f"/download/{key}"

    def get(self, key: str) -> Path | None:
        with self._lock:
            return self._files.get(key)


@dataclass
class Assistant:
    """The modes, with their dependencies injected (tests pass fakes)."""

    bindings: Bindings
    clients: Callable[[], list[str]]
    modes: Sequence[str]
    # mode -> (scope, message, history) -> text, or {"text": ..., "sources": [...]}
    run: Mapping[str, Callable[[str, str, list[dict[str, str]]], Any]]
    downloads: Downloads | None = None

    def state(self, chat_id: str) -> dict[str, Any]:
        client = self.bindings.client_of(chat_id)
        return {
            "client": client,
            "modes": list(self.modes),
            "clients": [{"scope": s, "name": display_name(s)} for s in self.clients()],
        }

    def start(self, chat_id: str, client: str) -> dict[str, Any]:
        if client not in self.clients():
            raise BindingError("unknown client")
        self.bindings.bind(chat_id, client)
        return {"client": client}

    def ask(
        self, chat_id: str, client: str, mode: str, message: str, history: list[dict[str, str]]
    ) -> dict[str, Any]:
        self.bindings.check(chat_id, client)
        if mode not in self.modes or mode not in self.run:
            raise ValueError(f"the {mode} mode is not available")
        result = self.run[mode](client, message, history)
        return result if isinstance(result, dict) else {"text": str(result), "sources": []}


class Modes(Protocol):
    @property
    def downloads(self) -> Downloads | None: ...

    def state(self, chat_id: str) -> dict[str, Any]: ...
    def start(self, chat_id: str, client: str) -> dict[str, Any]: ...
    def ask(
        self, chat_id: str, client: str, mode: str, message: str, history: list[dict[str, str]]
    ) -> dict[str, Any]: ...


class LazyAssistant:
    """Builds the real assistant on the first request, so Start AI stays fast (T6.AC1)."""

    def __init__(self, factory: Callable[[], Assistant]) -> None:
        self._factory = factory
        self._built: Assistant | None = None
        self._lock = threading.Lock()

    def _get(self) -> Assistant:
        with self._lock:
            if self._built is None:
                self._built = self._factory()
            return self._built

    @property
    def downloads(self) -> Downloads | None:
        """Downloads exist only after a summary was exported, so never build just for this."""
        with self._lock:
            return self._built.downloads if self._built is not None else None

    def state(self, chat_id: str) -> dict[str, Any]:
        return self._get().state(chat_id)

    def start(self, chat_id: str, client: str) -> dict[str, Any]:
        return self._get().start(chat_id, client)

    def ask(
        self, chat_id: str, client: str, mode: str, message: str, history: list[dict[str, str]]
    ) -> dict[str, Any]:
        return self._get().ask(chat_id, client, mode, message, history)


def _handler(assistant: Modes, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:  # no content in logs (T8)
            return

        def _reply(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self) -> bool:
            given = self.headers.get("X-Company-Brain-Token", "")
            return hmac.compare_digest(given, token)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._reply(HTTPStatus.OK, {"ok": True})
            elif self.path.startswith("/download/") and assistant.downloads is not None:
                found = assistant.downloads.get(self.path.removeprefix("/download/"))
                if found is None or not found.is_file():
                    self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                data = found.read_bytes()
                kind = (
                    "application/pdf"
                    if found.suffix == ".pdf"
                    else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Disposition", f'attachment; filename="{found.name}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorised():
                self._reply(HTTPStatus.UNAUTHORIZED, {"error": "not authorised"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._reply(HTTPStatus.BAD_REQUEST, {"error": "bad length"})
                return
            if length < 0 or length > MAX_BODY:  # refused before reading (T46 F-01)
                self.close_connection = True
                self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request too large"})
                return
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
                chat_id = str(data.get("chat_id", ""))
                if self.path == "/chat/state":
                    result = assistant.state(chat_id)
                elif self.path == "/chats":
                    result = assistant.start(chat_id, str(data.get("client", "")))
                elif self.path == "/ask":
                    result = assistant.ask(
                        chat_id,
                        str(data.get("client", "")),
                        str(data.get("mode", "")),
                        str(data.get("message", "")),
                        list(data.get("history", [])),
                    )
                else:
                    self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
            except BindingError as error:
                self._reply(HTTPStatus.FORBIDDEN, {"error": str(error)})
            except (ValueError, json.JSONDecodeError) as error:
                self._reply(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except Exception:  # noqa: BLE001 - never send a stack trace to the chat
                self._reply(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error"})
            else:
                self._reply(HTTPStatus.OK, result)

    return Handler


def serve(assistant: Modes, port: int, token: str) -> ThreadingHTTPServer:
    if not token:
        raise ValueError("the service needs a token")  # an empty one would match a missing header
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(assistant, token))
    server.daemon_threads = True
    return server


def token_path(data_dir: Path) -> Path:
    return data_dir / TOKEN_FILE


def ensure_token(data_dir: Path) -> str:
    """The shared secret between the Pipe and this service (file readable by the owner only)."""
    path = token_path(data_dir)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


def build_assistant(settings: Any) -> Assistant:  # pragma: no cover - wiring, tested end to end
    """The real modes: search, rerank, answer and gate; summaries of the best-matching file."""
    import tomllib

    from company_brain import (
        answer,
        draft,
        export,
        followups,
        guardrails,
        rerank,
        retrieval,
        summarise,
    )
    from company_brain.content_guard import GuardedReader, Manifest
    from company_brain.convert import Docling, convert
    from company_brain.db import connect
    from company_brain.drive import load_sources
    from company_brain.embedding import EmbeddingClient, active_index, load_embedding_models
    from company_brain.questions import NOT_FOUND

    repo = Path(__file__).resolve().parents[2]
    conn = connect(settings)
    lock = threading.Lock()
    root = settings.root or load_sources(repo / "config" / "sources.toml")["root"]
    reader = GuardedReader(root, Manifest.load(repo / "data" / "manifests"))
    profile = tomllib.loads((repo / "config" / "profile.toml").read_text())["demo_profile"]
    with lock:
        index = active_index(conn)
        model_name = conn.execute(
            "SELECT model FROM brain.index_meta WHERE index_id = %s", (index,)
        ).fetchone()
        conn.commit()
    embedder = EmbeddingClient(
        load_embedding_models()[model_name[0] if model_name else "bge-m3:latest"]
    )
    reranker = rerank.Reranker()
    claim_score = guardrails.hhem_scorer()
    docling = Docling()
    downloads = Downloads(export.check_export_dir(settings.exports_dir))

    def clients() -> list[str]:
        with lock:
            rows = conn.execute(
                "SELECT DISTINCT scope FROM brain.documents WHERE scope <> %s "
                "AND client NOT IN (SELECT client FROM brain.tombstones) ORDER BY scope",
                (SHARED,),
            ).fetchall()
            conn.commit()
        return [row[0] for row in rows]

    def search(scope: str, message: str, history: list[dict[str, str]]) -> tuple[str, Any]:
        turns = [followups.Turn(t.get("role", "user"), t.get("content", "")) for t in history]
        question = followups.rewrite(message, turns, followups.ollama_generate)
        with lock:
            hits = retrieval.retrieve(
                conn, embedder.embed_query(question), question, scope=scope, chat_scope=scope
            )
            pairs = retrieval.counterpart_map(conn, scope, scope)
            conn.commit()
        return question, (hits, pairs)

    def answer_mode(scope: str, message: str, history: list[dict[str, str]]) -> dict[str, Any]:
        question, (hits, pairs) = search(scope, message, history)
        selection = reranker.rerank(question, hits[:40], counterparts=pairs)
        gated = answer.respond(question, selection, answer.ollama_generate, claim_score=claim_score)
        return {"text": gated.text, "sources": gated.sources}

    def summarise_mode(scope: str, message: str, history: list[dict[str, str]]) -> Any:
        question, (hits, _pairs) = search(scope, message, history)
        picked = summarise.pick_document(question, [h for h in hits if h.scope == scope])
        if picked is None:
            return NOT_FOUND[rerank.language_of(question)]
        target = picked.rel_path
        converted = convert(reader.read(Path(root) / target), docling)
        result = summarise.summarise(converted, summarise.ollama_generate)
        title = f"Summary of {Path(target).name}"
        links = []
        if "draft" in modes and result.gated is not None:  # export is in the full profile
            word, pdf = export.export(result.gated, title, settings.exports_dir, owner=scope)
            base = f"http://127.0.0.1:{settings.backend_port}"
            links = [
                {"label": "Word", "url": base + downloads.add(word)},
                {"label": "PDF", "url": base + downloads.add(pdf)},
            ]
        return {"text": f"{title}:\n\n{result.text}", "sources": result.sources, "downloads": links}

    def draft_mode(scope: str, message: str, history: list[dict[str, str]]) -> Any:
        question, (hits, pairs) = search(scope, message, history)
        selection = reranker.rerank(question, hits[:40], counterparts=pairs, threshold=-1e9)
        result = draft.draft(question, selection, draft.ollama_generate)
        title = (
            "Proposal draft" if rerank.language_of(question) == "en" else "Borrador de propuesta"
        )
        links = []
        if result.readable:
            word, pdf = export.export(result.gated(), title, settings.exports_dir, owner=scope)
            base = f"http://127.0.0.1:{settings.backend_port}"
            links = [
                {"label": "Word", "url": base + downloads.add(word)},
                {"label": "PDF", "url": base + downloads.add(pdf)},
            ]
        return {"text": f"{title}\n\n{result.text}", "sources": result.sources, "downloads": links}

    modes = MODES_BY_PROFILE.get(profile, MODES_BY_PROFILE["lite"])
    run = {"answer": answer_mode, "summarise": summarise_mode}
    if "draft" in modes:
        run["draft"] = draft_mode
    return Assistant(Bindings(DatabaseStore(connect(settings))), clients, modes, run, downloads)


def main() -> None:  # pragma: no cover - started by launchd
    from company_brain.settings import Settings

    settings = Settings.from_env()
    token = ensure_token(settings.data_dir)
    lazy = LazyAssistant(lambda: build_assistant(settings))
    server = serve(lazy, settings.backend_port, token)
    server.serve_forever()


if __name__ == "__main__":
    main()
