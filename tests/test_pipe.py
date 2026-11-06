"""T29: client binding (backend) and the Open WebUI Pipe (fake backend).

PBT for AC3: for any chat and client that differ from the stored binding, the
request is rejected; a chat cannot be re-bound to another client (protects
against one client's documents answering in another client's chat).
"""

import asyncio
import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from company_brain import backend
from company_brain.openwebui_pipe import Pipe

ids = st.text("abcdef0123", min_size=1, max_size=6)
scopes = st.sampled_from(
    ["fictional:Pellmore Dental Group", "fictional:Quillstone Hotels", "public"]
)


@given(ids, scopes, ids, scopes)
def test_requests_must_match_the_binding(
    chat: str, scope: str, other_chat: str, other_scope: str
) -> None:
    """T29.AC3."""
    bindings = backend.Bindings(backend.MemoryStore())
    bindings.bind(chat, scope)
    bindings.check(chat, scope)
    if (other_chat, other_scope) != (chat, scope):
        with pytest.raises(backend.BindingError):
            bindings.check(other_chat, other_scope)
    if other_scope != scope:
        with pytest.raises(backend.BindingError):
            bindings.bind(chat, other_scope)
    with pytest.raises(backend.BindingError):
        bindings.check("", scope)


class FakeBackend:
    def __init__(self, client: str | None = None, fail: Exception | None = None) -> None:
        self.client, self.fail, self.calls = client, fail, []

    async def __call__(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, payload))
        if self.fail is not None:
            raise self.fail
        if path == "/chat/state":
            return {
                "client": self.client,
                "clients": [
                    {"scope": "fictional:Pellmore Dental Group", "name": "Pellmore Dental Group"},
                    {"scope": "fictional:Quillstone Hotels", "name": "Quillstone Hotels"},
                ],
            }
        if path == "/chats":
            self.client = payload["client"]
            return {"client": self.client}
        if path == "/ask":
            return {"text": "The fee is EUR 36,500. [1]"}
        raise AssertionError(path)


def run(pipe: Pipe, text: str, mode: str = "answer", chat: str = "chat-1") -> str:
    body = {"model": f"company_brain.{mode}", "messages": [{"role": "user", "content": text}]}
    return asyncio.run(pipe.pipe(body, __metadata__={"chat_id": chat}))


def test_no_client_asks_which_client_and_does_not_search() -> None:
    """T29.AC2."""
    pipe, fake = Pipe(), FakeBackend()
    pipe._call = fake  # type: ignore[method-assign]
    reply = run(pipe, "What fee did we agree?")
    assert reply.startswith("Which client?") and "1. Pellmore Dental Group" in reply
    assert [path for path, _ in fake.calls] == ["/chat/state"]


def test_choosing_a_client_binds_the_chat_then_questions_are_answered() -> None:
    pipe, fake = Pipe(), FakeBackend()
    pipe._call = fake  # type: ignore[method-assign]
    assert "Quillstone Hotels" in run(pipe, "2")
    assert fake.calls[-1] == (
        "/chats",
        {"chat_id": "chat-1", "client": "fictional:Quillstone Hotels"},
    )
    assert run(pipe, "What fee did we agree?") == "The fee is EUR 36,500. [1]"
    path, payload = fake.calls[-1]
    assert (
        path == "/ask"
        and payload["client"] == "fictional:Quillstone Hotels"
        and payload["mode"] == "answer"
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), "took too long"),
        (TimeoutError(), "took too long"),
        (ConnectionRefusedError(), "is not running"),
        (PermissionError("forged"), "another client"),
    ],
)
def test_errors_are_plain_english(error: Exception, expected: str) -> None:
    """T29.AC4 (and AC3 as the user sees it)."""
    pipe = Pipe()
    pipe._call = FakeBackend(client="fictional:Pellmore Dental Group", fail=error)  # type: ignore[method-assign]
    reply = run(pipe, "What fee did we agree?")
    assert expected in reply and "Traceback" not in reply and "Error" not in reply


def test_a_chat_without_an_id_is_refused() -> None:
    pipe, fake = Pipe(), FakeBackend()
    pipe._call = fake  # type: ignore[method-assign]
    body = {"model": "company_brain.answer", "messages": [{"role": "user", "content": "Hi"}]}
    assert "new chat" in asyncio.run(pipe.pipe(body, __metadata__={})).lower()
    assert fake.calls == []
    assert json.dumps(Pipe().pipes())


def test_sources_are_sent_for_every_citation() -> None:
    """T30.AC1 (Pipe side)."""
    sent: list[dict] = []  # type: ignore[type-arg]

    async def emitter(event: dict) -> None:  # type: ignore[type-arg]
        sent.append(event)

    class WithSources(FakeBackend):
        async def __call__(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path == "/ask":
                return {
                    "text": "The fee is EUR 36,500. [1]\nPayment within 45 days. [2]",
                    "sources": [
                        {
                            "n": 1,
                            "name": "Agreement.docx",
                            "location": "clause 3",
                            "excerpt": "The fee is EUR 36,500.",
                        },
                        {
                            "n": 2,
                            "name": "Agreement.docx",
                            "location": "clause 4",
                            "excerpt": "Payment within 45 days.",
                        },
                    ],
                }
            return await super().__call__(path, payload)

    pipe = Pipe()
    pipe._call = WithSources(client="fictional:Pellmore Dental Group")  # type: ignore[method-assign]
    body = {"model": "company_brain.answer", "messages": [{"role": "user", "content": "Fee?"}]}
    asyncio.run(pipe.pipe(body, __metadata__={"chat_id": "c1"}, __event_emitter__=emitter))
    names = [e["data"]["source"]["name"] for e in sent if e["type"] == "source"]
    assert names == ["[1] Agreement.docx, clause 3", "[2] Agreement.docx, clause 4"]
    assert all(e["data"]["document"] for e in sent)


def test_download_links_follow_the_summary() -> None:
    """T31: the reply shows Word and PDF download links when the service exported the summary."""

    class WithDownloads(FakeBackend):
        async def __call__(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path == "/ask":
                return {
                    "text": "Summary of Call.vtt:\n\nDecision: x. [1]",
                    "sources": [],
                    "downloads": [
                        {"label": "Word", "url": "http://127.0.0.1:8765/download/a"},
                        {"label": "PDF", "url": "http://127.0.0.1:8765/download/b"},
                    ],
                }
            return await super().__call__(path, payload)

    pipe = Pipe()
    pipe._call = WithDownloads(client="public")  # type: ignore[method-assign]
    reply = run(pipe, "Summarise the call", mode="summarise")
    assert reply.endswith(
        "Download: [Word](http://127.0.0.1:8765/download/a) · [PDF](http://127.0.0.1:8765/download/b)"
    )
