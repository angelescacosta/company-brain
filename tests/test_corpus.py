"""T11: the fictional test corpus.

The corpus is built into a temporary folder and read back with plain readers
(python-docx, openpyxl, pdfium, a small WebVTT parser), never Docling, so the
ground truth is checked independently of the converter T17 tests. Dates and
amounts are rendered here by the test's own formatter, not the builder's.
"""

import hashlib
import json
import re
import tomllib
from datetime import date
from pathlib import Path

import openpyxl
import pypdfium2 as pdfium
import pytest
from docx import Document
from fontTools.ttLib import TTFont

from company_brain import corpus
from company_brain.corpus_data import CLIENTS

REPO = Path(__file__).resolve().parents[1]
ROLES = {
    "call-vtt": {".vtt"},
    "call-docx": {".docx"},
    "proposal": {".pdf"},
    "contract": {".docx"},
    "draft": {".docx"},
    "report": {".pdf", ".pptx", ".xlsx"},
}
MONTHS = {
    "en": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
    "es": [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ],
}


def spoken_date(iso: str, language: str) -> str:
    day = date.fromisoformat(iso)
    month = MONTHS[language][day.month - 1]
    if language == "en":
        return f"{day.day} {month} {day.year}"
    return f"{day.day} de {month} de {day.year}"


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, corpus.Built]:
    out = tmp_path_factory.mktemp("corpus")
    return out, corpus.build(out)


def on_disk(out: Path, relative: str) -> Path:
    assert relative.startswith(corpus.PREFIX + "/")
    return out / relative[len(corpus.PREFIX) + 1 :]


def vtt_cues(path: Path) -> list[tuple[str, str]]:
    blocks = path.read_text(encoding="utf-8").split("\n\n")
    cues = []
    for block in blocks:
        lines = block.strip().splitlines()
        if lines and "-->" in lines[0]:
            cues.append((lines[0].split(" --> ")[0], " ".join(lines[1:])))
    return cues


def whole_text(path: Path) -> str:
    if path.suffix == ".vtt":
        return path.read_text(encoding="utf-8")
    if path.suffix == ".docx":
        return "\n".join(p.text for p in Document(str(path)).paragraphs)
    if path.suffix == ".pdf":
        pdf = pdfium.PdfDocument(str(path))
        return "\n".join(page.get_textpage().get_text_range() for page in pdf)
    if path.suffix == ".xlsx":
        book = openpyxl.load_workbook(path)
        return "\n".join(
            str(cell.value)
            for sheet in book
            for row in sheet.iter_rows()
            for cell in row
            if cell.value is not None
        )
    raise AssertionError(path.suffix)


def text_at(out: Path, location: dict) -> str:
    path = on_disk(out, location["file"])
    if "cue" in location:
        start, text = vtt_cues(path)[location["cue"] - 1]
        assert start == location["start"]
        return text
    if "paragraph" in location:
        return Document(str(path)).paragraphs[location["paragraph"]].text
    if "page" in location:
        page = pdfium.PdfDocument(str(path))[location["page"] - 1]
        return page.get_textpage().get_text_range()
    if "cell" in location:
        sheet, cells = location["cell"].split("!")
        found = openpyxl.load_workbook(path)[sheet][cells]
        row = found[0] if isinstance(found[0], tuple) else (found,)
        return " ".join(str(cell.value) for cell in row)
    raise AssertionError(location)


def test_every_client_has_every_document_type(built: tuple[Path, corpus.Built]) -> None:
    """T11.AC1, plus the language mix, the scanned page and the planted documents."""
    _out, result = built
    languages = [c.language for c in CLIENTS]
    assert languages.count("en") == 3 and languages.count("es") == 3
    assert [c.key for c in CLIENTS if c.mixed_language] and all(
        c.language == "en" for c in CLIENTS if c.mixed_language
    )
    for client in CLIENTS:
        files = [f for f in result.files if f["client"] == client.key]
        for role, suffixes in ROLES.items():
            matching = [f for f in files if f["role"] == role]
            assert len(matching) == 1, (client.key, role)
            assert Path(matching[0]["path"]).suffix in suffixes
    assert len([f for f in result.files if f["role"] == "scanned"]) == 1
    assert len({f["client"] for f in result.files if f["role"] == "planted"}) == 2


def test_every_file_is_marked_fictional(built: tuple[Path, corpus.Built]) -> None:
    out, result = built
    for entry in result.files:
        path = on_disk(out, entry["path"])
        if entry["role"] == "scanned":
            pdf = pdfium.PdfDocument(str(path))
            assert squash(pdf[0].get_textpage().get_text_range()) == ""  # image only
            client = next(c for c in CLIENTS if c.key == entry["client"])
            assert "FICTIONAL" in client.scanned[0]
        else:
            assert "FICTIONAL" in whole_text(path), entry["path"]


def test_every_fact_is_at_its_location(built: tuple[Path, corpus.Built]) -> None:
    """T11.AC2: each typed record's values appear at the recorded location."""
    out, result = built
    kinds = {fact["type"] for fact in result.facts}
    assert {"value", "decision", "action", "chart", "planted"} <= kinds
    for fact in result.facts:
        language = fact["language"]
        location = fact["location"]
        if fact["type"] == "value" and location.get("scanned"):
            client = next(c for c in CLIENTS if c.key == fact["client"])
            assert any(fact["text"] in line for line in client.scanned)
            continue
        here = squash(text_at(out, location))
        if fact["type"] == "value":
            assert fact["text"] in here, (fact["id"], here)
            if fact.get("unit") == "EUR":
                assert re.sub(r"\D", "", fact["text"]) == str(fact["value"])
            if fact.get("unit") == "date":
                assert fact["text"] == spoken_date(fact["value"], language)
        elif fact["type"] == "decision":
            assert fact["decision"] in here, fact["id"]
        elif fact["type"] == "action":
            for part in (fact["action"], fact["owner"], spoken_date(fact["due"], fact["spoken"])):
                assert part in here, (fact["id"], part, here)
        elif fact["type"] == "chart":
            assert fact["label"] in here and str(fact["value"]) in here, fact["id"]
        elif fact["type"] == "planted":
            assert fact["text"] in here, fact["id"]
        else:
            raise AssertionError(fact["type"])


def test_superseded_values_conflict_with_signed_ones(built: tuple[Path, corpus.Built]) -> None:
    _out, result = built
    by_id = {f["id"]: f for f in result.facts}
    drafts = [f for f in result.facts if f.get("status") == "superseded"]
    assert len({f["client"] for f in drafts}) == len(CLIENTS)
    for draft in drafts:
        signed = by_id[draft["counterpart"]]
        assert signed["status"] == "current" and signed["counterpart"] == draft["id"]
        assert signed["attribute"] == draft["attribute"]
        assert signed["value"] != draft["value"]


def test_names_are_not_known_companies_or_public_parties(built: tuple[Path, corpus.Built]) -> None:
    """T11.AC3: checked against a list of well-known companies and the public test data.

    This is not a claim of global uniqueness. The public manifest exists once T12
    has run; CHK5 repeats this check from then on.
    """
    known = {
        line.strip().casefold()
        for line in (REPO / "tests" / "fixtures" / "well_known_companies.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.startswith("#")
    }
    public = REPO / "data" / "manifests" / "public.json"
    if public.exists():
        for entry in json.loads(public.read_text(encoding="utf-8"))["files"]:
            known.update(name.casefold() for name in entry.get("parties", []))
    assert len(known) >= 100
    for client in CLIENTS:
        for name in (client.key, client.name):
            folded = name.casefold()
            clashes = [k for k in known if re.search(rf"\b{re.escape(k)}\b", folded)]
            assert not clashes, (name, clashes)


def test_manifest_and_facts_are_committed_and_reproducible(
    built: tuple[Path, corpus.Built], tmp_path: Path
) -> None:
    """T11.AC4: the committed manifest lists every file with its hash, and a rebuild matches."""
    out, result = built
    for entry in result.files:
        data = on_disk(out, entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    written = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file())
    assert written == sorted(
        on_disk(out, e["path"]).relative_to(out).as_posix() for e in result.files
    )
    again = corpus.build(tmp_path)
    assert again.files == result.files and again.facts == result.facts
    manifest = json.loads((REPO / "data" / "manifests" / "fictional.json").read_text())
    committed = [e for e in manifest["files"] if e["task"] == "T11"]
    assert committed == result.files
    facts = json.loads((REPO / "data" / "facts.json").read_text())
    assert [f for f in facts["facts"] if f["task"] == "T11"] == result.facts


def test_scan_font_is_pinned_and_has_every_letter() -> None:
    """The scanned page is drawn in a pinned open-licence font that has every letter used."""
    fonts = tomllib.loads((REPO / "config" / "fonts.toml").read_text(encoding="utf-8"))["font"]
    entry = next(f for f in fonts if Path(f["file"]) == corpus.SCAN_FONT.relative_to(REPO))
    assert hashlib.sha256(corpus.SCAN_FONT.read_bytes()).hexdigest() == entry["sha256"]
    covered = TTFont(str(corpus.SCAN_FONT)).getBestCmap()
    for client in CLIENTS:
        for line in client.scanned:
            missing = {ch for ch in line if ord(ch) not in covered}
            assert not missing, (line, missing)


def test_every_fictional_file_is_where_the_scanner_looks() -> None:
    """T11/T15 consistency: the owner's layout Clients/<client>/{Transcripts,...}/file."""
    from pathlib import PurePosixPath

    from company_brain.scanner import location_of

    manifest = json.loads((REPO / "data" / "manifests" / "fictional.json").read_text())
    for entry in manifest["files"]:
        location = location_of(PurePosixPath(entry["path"]).parts)
        assert location is not None, entry["path"]
        if entry["task"] in {"T11", "T11R"}:
            assert location.client == entry["client"], entry["path"]
