"""T30: sources under each answer, and Open WebUI's own retrieval never called.

Drives the test Open WebUI in a real browser (Playwright, Chromium): sign in,
open Company Brain, choose the client, ask. Every citation must have a source
entry under the answer (AC1), and the browser's request log must hold no call
to Open WebUI's retrieval endpoints during the chat (AC2).
"""

import contextlib
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from owui_harness import EMAIL, bring_up

from company_brain.services import Launchd, jobs_dir
from company_brain.services import stop as stop_services
from company_brain.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture
def settings() -> Iterator[Settings]:
    settings = Settings.from_env()
    yield settings
    stop_services(settings, Launchd(jobs_dir(settings)))


def test_sources_shown_and_no_openwebui_retrieval(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from playwright.sync_api import expect, sync_playwright

    up = bring_up(settings, monkeypatch, "Pellmore")
    requests: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.on("request", lambda r: requests.append(r.url))
        try:
            page.goto(f"{up.url}/auth")
            page.locator("input[type=email], input[autocomplete=email]").first.fill(EMAIL)
            page.locator("input[type=password]").first.fill(up.password)
            page.keyboard.press("Enter")
            page.wait_for_url(re.compile(r".*/(c/.*)?$"), timeout=60_000)
            page.goto(f"{up.url}/?model=company_brain.answer")
            welcome = page.get_by_text("Okay, Let's Go!")
            with contextlib.suppress(Exception):  # the "What's New" notice, not always shown
                welcome.click(timeout=10_000)
            box = page.locator("#chat-input")

            def say(text: str, expect_text: str) -> None:
                box.click()
                page.keyboard.type(text)
                page.keyboard.press("Enter")
                expect(page.get_by_text(expect_text).last).to_be_visible(timeout=300_000)

            requests.clear()
            say("Hello", "Which client?")
            say("Pellmore Dental Group (fictional)", "This chat is now about")
            say("What fee did we agree with Pellmore Dental Group?", "36,500")
            page.wait_for_timeout(2_000)
            content = page.content()
            cited = sorted(
                set(re.findall(r"\[(\d+)\]", page.locator("#messages-container").inner_text()))
            )
            assert cited, "no citation in the answer"
            for number in cited:
                entry = re.search(rf"\[{number}\] [^<\[]*\.(?:docx|vtt|xlsx|pdf)", content)
                assert entry, f"no source entry with a file name for [{number}]"
            assert not [u for u in requests if "/api/v1/retrieval" in u], requests
        except Exception:
            page.screenshot(path=str(tmp_path / "sources_display_failure.png"), full_page=True)
            raise
        finally:
            browser.close()
