"""T29: the local service over HTTP (fake modes) and the database binding store."""

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator

import pytest

from company_brain import backend

TOKEN = "test-token"  # noqa: S105


@pytest.fixture
def server() -> Iterator[str]:
    assistant = backend.Assistant(
        bindings=backend.Bindings(backend.MemoryStore()),
        clients=lambda: ["fictional:Pellmore Dental Group", "public"],
        modes=("answer", "summarise", "draft"),
        run={
            "answer": lambda scope, msg, _h: f"{scope}: {msg}",
            "summarise": lambda _s, _m, _h: 1 / 0,
        },  # type: ignore[dict-item]
    )
    httpd = backend.serve(assistant, 0, TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def post(url: str, path: str, payload: dict, token: str = TOKEN) -> tuple[int, dict]:  # type: ignore[type-arg]
    request = urllib.request.Request(  # noqa: S310 - the test server on 127.0.0.1
        url + path,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"X-Company-Brain-Token": token},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_token_binding_and_modes(server: str) -> None:
    assert post(server, "/chat/state", {"chat_id": "c1"}, token="wrong")[0] == 401  # noqa: S106
    status, state = post(server, "/chat/state", {"chat_id": "c1"})
    assert status == 200 and state["client"] is None and len(state["clients"]) == 2
    assert post(server, "/chats", {"chat_id": "c1", "client": "nobody"})[0] == 403
    assert post(server, "/chats", {"chat_id": "c1", "client": "public"})[0] == 200
    assert (
        post(server, "/chats", {"chat_id": "c1", "client": "fictional:Pellmore Dental Group"})[0]
        == 403
    )
    ok = post(
        server, "/ask", {"chat_id": "c1", "client": "public", "mode": "answer", "message": "hi"}
    )
    assert ok == (200, {"text": "public: hi", "sources": []})
    forged = post(
        server,
        "/ask",
        {
            "chat_id": "c1",
            "client": "fictional:Pellmore Dental Group",
            "mode": "answer",
            "message": "hi",
        },
    )
    assert forged[0] == 403
    assert (
        post(
            server, "/ask", {"chat_id": "c2", "client": "public", "mode": "answer", "message": "hi"}
        )[0]
        == 403
    )
    assert (
        post(
            server, "/ask", {"chat_id": "c1", "client": "public", "mode": "draft", "message": "hi"}
        )[0]
        == 400
    )
    crash = post(
        server, "/ask", {"chat_id": "c1", "client": "public", "mode": "summarise", "message": "hi"}
    )
    assert crash == (500, {"error": "internal error"})


@pytest.mark.integration
def test_database_store_keeps_the_first_binding() -> None:
    from ingest_helpers import fresh_db

    conn = fresh_db()
    bindings = backend.Bindings(backend.DatabaseStore(conn))
    bindings.bind("chat-9", "public")
    with pytest.raises(backend.BindingError):
        bindings.bind("chat-9", "fictional:Quillstone Hotels")
    bindings.check("chat-9", "public")
    assert backend.Bindings(backend.DatabaseStore(conn)).client_of("chat-9") == "public"
    conn.close()


def test_token_file_is_private(tmp_path) -> None:  # type: ignore[no-untyped-def]
    token = backend.ensure_token(tmp_path)
    assert backend.ensure_token(tmp_path) == token
    assert oct(backend.token_path(tmp_path).stat().st_mode)[-3:] == "600"


def test_downloads_are_served_only_from_the_export_folder(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """T31: the download link gives the file; unknown IDs and other folders are refused."""
    exports = tmp_path / "exports"
    exports.mkdir()
    word = exports / "Summary.docx"
    word.write_bytes(b"PK fake docx")
    downloads = backend.Downloads(exports)
    link = downloads.add(word)
    with pytest.raises(ValueError):
        downloads.add(tmp_path / "elsewhere.docx")
    assistant = backend.Assistant(
        backend.Bindings(backend.MemoryStore()), lambda: [], (), {}, downloads=downloads
    )
    httpd = backend.serve(assistant, 0, TOKEN)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    with urllib.request.urlopen(url + link, timeout=10) as response:  # noqa: S310
        assert response.read() == b"PK fake docx"
        assert 'attachment; filename="Summary.docx"' in response.headers["Content-Disposition"]
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(url + "/download/not-a-real-id", timeout=10)  # noqa: S310
    httpd.shutdown()


def test_oversized_requests_are_refused_before_reading(server: str) -> None:
    """T46 F-01 (LLM10, unbounded consumption): a request body over the limit, a claimed
    length over the limit, or a negative length is refused without being read."""
    big = {"chat_id": "c9", "client": "public", "mode": "answer", "message": "x" * (2 << 20)}
    try:
        refused = post(server, "/ask", big)[0] == 413
    except urllib.error.URLError as error:  # closed before the client finished sending
        refused = isinstance(error.reason, (BrokenPipeError, ConnectionResetError))
    assert refused
    import http.client

    host, port = server.removeprefix("http://").split(":")
    for claimed in (str(64 << 20), "-1"):
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        conn.putrequest("POST", "/chat/state")
        conn.putheader("X-Company-Brain-Token", TOKEN)
        conn.putheader("Content-Length", claimed)
        conn.endheaders()
        assert conn.getresponse().status in (400, 413), claimed
        conn.close()
    post(server, "/chats", {"chat_id": "c9", "client": "public"})
    ok = {"chat_id": "c9", "client": "public", "mode": "answer", "message": "x" * 10_000}
    assert post(server, "/ask", ok)[0] == 200  # ordinary long questions still work
