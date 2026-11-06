"""T32: the owner's flow in a real browser (Playwright, Chromium) on the test copies.

Sign in, choose a fictional client, ask and open a source, summarise a call,
and in the full profile draft a proposal; click the Word and PDF download links
and open the files. Every reply must show the output gate's marks (citations,
the suggestions box, no stack trace). Parametrised by profile: the profile in
`config/profile.toml` runs, the other is skipped.
"""

import contextlib
import io
import re
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from docx import Document

from company_brain.services import Launchd, jobs_dir
from company_brain.services import stop as stop_services
from company_brain.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
from owui_harness import EMAIL, bring_up  # noqa: E402

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]
PROFILE = tomllib.loads((REPO / "config" / "profile.toml").read_text())["demo_profile"]


@pytest.fixture
def settings() -> Iterator[Settings]:
    settings = Settings.from_env()
    yield settings
    stop_services(settings, Launchd(jobs_dir(settings)))


def file_text(name: str, data: bytes) -> str:
    if name.endswith(".docx"):
        return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)
    pages = pdfium.PdfDocument(data)
    return " ".join(pages[i].get_textpage().get_text_range() for i in range(len(pages)))


@pytest.mark.parametrize("profile", ["lite", "full"])
def test_owner_flow(
    profile: str, settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    if profile != PROFILE:
        pytest.skip(f"the configured profile is {PROFILE}")
    from playwright.sync_api import expect, sync_playwright

    up = bring_up(settings, monkeypatch, "Pellmore")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            page.goto(f"{up.url}/auth")
            page.locator("input[type=email], input[autocomplete=email]").first.fill(EMAIL)
            page.locator("input[type=password]").first.fill(up.password)
            page.keyboard.press("Enter")
            page.wait_for_url(re.compile(r".*/(c/.*)?$"), timeout=60_000)

            def open_chat(mode: str) -> None:
                page.goto(f"{up.url}/?model=company_brain.{mode}")
                with contextlib.suppress(Exception):  # the "What's New" notice, not always shown
                    page.get_by_text("Okay, Let's Go!").click(timeout=5_000)

            def say(text: str, expected: str) -> None:
                page.locator("#chat-input").click()
                page.keyboard.type(text)
                page.keyboard.press("Enter")
                expect(page.get_by_text(expected).last).to_be_visible(timeout=600_000)

            def download(label: str) -> str:
                link = page.locator(f"a:has-text('{label}')").last
                href = link.get_attribute("href") or ""
                assert "/download/" in href, href
                response = page.request.get(href)
                assert response.ok, response.status
                disposition = response.headers.get("content-disposition", "")
                name = re.search(r'filename="([^"]+)"', disposition)
                assert name, disposition
                return file_text(name.group(1), response.body())

            open_chat("answer")
            say("Hello", "Which client?")
            say("Pellmore Dental Group (fictional)", "This chat is now about")
            say("What fee did we agree with Pellmore Dental Group?", "36,500")
            source = page.locator("button:has-text('[1] ')").last
            source.click()
            expect(page.get_by_text(re.compile(r"36,500|fee")).last).to_be_visible(timeout=30_000)
            page.keyboard.press("Escape")

            open_chat("summarise")
            say("Pellmore Dental Group (fictional)", "This chat is now about")
            say("Summarise the call of 27 August 2026.", "Summary of")
            if profile == "full":
                assert "Summary of" in download("Word") and "Solihull" in download("PDF")

            if profile == "full":
                open_chat("draft")
                say("Pellmore Dental Group (fictional)", "This chat is now about")
                say("Draft a proposal for Pellmore Dental Group.", "Optional retainer")
                text = page.locator("#messages-container").inner_text()
                assert "Suggestions, not agreed" in text  # the gate's box for proposals
                assert "Options" in download("Word") and "Options" in download("PDF")
            assert "Traceback" not in page.content()
        except Exception:
            page.screenshot(path=str(tmp_path / "owner_flow_failure.png"), full_page=True)
            raise
        finally:
            browser.close()
