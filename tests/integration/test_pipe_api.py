"""T29.AC1 and AC2 through Open WebUI's own API, with test copies of every service.

A chat without a client gets "Which client?", a chosen client gives a checked,
cited answer, and Summarise mode returns a summary; the raw models stay hidden.
"""

import io
import re
import urllib.request
import uuid
from collections.abc import Iterator

import pypdfium2 as pdfium
import pytest
from docx import Document
from owui_harness import bring_up, call

from company_brain.services import Launchd, jobs_dir
from company_brain.services import stop as stop_services
from company_brain.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Iterator[Settings]:
    settings = Settings.from_env()
    yield settings
    stop_services(settings, Launchd(jobs_dir(settings)))


def test_chat_through_open_webui(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    up = bring_up(settings, monkeypatch, "Pellmore")
    models = [m["id"] for m in call(up.url, "/api/models", token=up.token)["data"]]
    assert {"company_brain.answer", "company_brain.summarise", "company_brain.draft"} <= set(models)
    assert not [m for m in models if not m.startswith("company_brain.")]  # raw models stay hidden

    def chat(model: str, chat_id: str, text: str) -> str:
        reply = call(
            up.url,
            "/api/chat/completions",
            {
                "model": model,
                "stream": False,
                "chat_id": chat_id,
                "id": str(uuid.uuid4()),
                "messages": [{"role": "user", "content": text}],
            },
            token=up.token,
        )
        return str(reply["choices"][0]["message"]["content"])

    def new_chat() -> str:
        payload = {"chat": {"title": "test", "messages": []}}
        return str(call(up.url, "/api/v1/chats/new", payload, token=up.token)["id"])

    first = new_chat()
    assert chat("company_brain.answer", first, "What fee did we agree?").startswith("Which client?")
    assert "Pellmore" in chat("company_brain.answer", first, "Pellmore Dental Group (fictional)")
    answer = chat(
        "company_brain.answer", first, "What fee did we agree with Pellmore Dental Group?"
    )
    assert "36,500" in answer and "[1]" in answer, answer
    second = new_chat()
    chat("company_brain.summarise", second, "Pellmore Dental Group (fictional)")
    summary = chat("company_brain.summarise", second, "Summarise the call of 27 August 2026.")
    assert summary.startswith("Summary of") and "Solihull" in summary, summary
    # T31: the full profile exports the checked summary; both downloads open.
    links = re.findall(r"\[(Word|PDF)\]\((http://127\.0\.0\.1:\d+/download/[\w-]+)\)", summary)
    assert [label for label, _ in links] == ["Word", "PDF"], summary
    for label, link in links:
        with urllib.request.urlopen(link, timeout=30) as response:  # noqa: S310 - test service
            data = response.read()
        if label == "Word":
            text = "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
        else:
            pages = pdfium.PdfDocument(data)
            text = " ".join(pages[i].get_textpage().get_text_range() for i in range(len(pages)))
        assert "Summary of" in text and "Solihull" in text and "[1]" in text, (label, text[:300])
    # T27 through Open WebUI (full profile): a draft with the methodology's structure and downloads.
    third = new_chat()
    chat("company_brain.draft", third, "Pellmore Dental Group (fictional)")
    proposal = chat("company_brain.draft", third, "Draft a proposal for Pellmore Dental Group.")
    assert proposal.startswith("Proposal draft") and "## Options" in proposal, proposal[:500]
    assert "## Optional retainer" in proposal and "[Word](" in proposal and "[PDF](" in proposal
