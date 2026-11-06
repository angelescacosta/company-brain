"""T17: conversion with provenance.

The fictional corpus and image fixtures are rebuilt into a temporary root
(byte-identical to the recorded ones) and read through the content guard, so
these tests never touch Drive. Docling runs are marked `slow` (model loading
and OCR take minutes); the rest run in every check.
"""

import hashlib
import io
import json
from pathlib import Path

import pytest
from fpdf import FPDF

from company_brain import convert as conv
from company_brain import corpus, image_fixtures
from company_brain.content_guard import GuardedReader, Manifest, VerifiedBytes

REPO = Path(__file__).resolve().parents[1]
CANONICAL = REPO / "tests" / "fixtures" / "images"
FACTS = [f for f in json.loads((REPO / "data" / "facts.json").read_text())["facts"]]


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("root")
    corpus.build(base / corpus.PREFIX)
    image_fixtures.build(base / corpus.PREFIX)
    # The listed image bytes, kept in the repository: re-encoding with a newer image library
    # can change a JPEG's bytes (seen with Pillow 13), and only listed bytes may be read.
    for image in CANONICAL.rglob("*"):
        if image.is_file():
            (base / image.relative_to(CANONICAL)).write_bytes(image.read_bytes())
    return base


def test_canonical_images_match_the_manifest() -> None:
    listed = {
        e["path"]: e["sha256"]
        for e in json.loads((REPO / "data" / "manifests" / "fictional.json").read_text())["files"]
        if e["task"] == "T11R"
    }
    kept = {
        p.relative_to(CANONICAL).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in CANONICAL.rglob("*")
        if p.is_file()
    }
    assert kept == listed


@pytest.fixture(scope="module")
def reader(root: Path) -> GuardedReader:
    return GuardedReader(root, Manifest.load(REPO / "data" / "manifests"))


def converted(reader: GuardedReader, root: Path, relative: str, docling=None) -> conv.Converted:  # type: ignore[no-untyped-def]
    result = conv.convert(reader.read(root / relative), docling)
    assert result.status == "converted", (relative, result.reason)
    return result


def facts_in(kind: str, key: str) -> list[dict]:
    return [
        f
        for f in FACTS
        if f["task"] == "T11"
        and kind in f.get("location", {}).get("file", "")
        and key in f.get("location", {})
    ]


# Fast: no Docling ------------------------------------------------------------------


def test_signature_mismatch_is_not_converted() -> None:
    """T17.AC9."""
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
    result = conv.convert(VerifiedBytes(relative="x/report.pdf", data=png, sha256="0"))
    assert result.status == "failed" and "not pdf" in result.reason
    assert conv.sniff(b"%PDF-1.7") == "pdf" and conv.sniff(b"WEBVTT\n\n") == "vtt"


def test_corrupt_and_encrypted_files_fail_and_the_run_continues(tmp_path: Path) -> None:
    """T17.AC5: each failure is recorded with a reason; later files still convert."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "FICTIONAL secret")
    pdf.set_encryption(owner_password="owner", user_password="user")  # noqa: S106
    encrypted = bytes(pdf.output())
    files = [
        VerifiedBytes(relative="a/encrypted.pdf", data=encrypted, sha256="1"),
        VerifiedBytes(relative="a/corrupt.pdf", data=b"%PDF-1.4\n\x00garbage", sha256="2"),
        VerifiedBytes(relative="a/broken.docx", data=b"PK\x03\x04broken", sha256="3"),
        VerifiedBytes(relative="a/notes.md", data=b"# FICTIONAL\n\nStill converted.\n", sha256="4"),
    ]
    results = [conv.convert(f) for f in files]
    assert [r.status for r in results] == ["failed", "failed", "failed", "converted"]
    assert all(r.reason and "secret" not in r.reason for r in results[:3])


def test_transcript_cues_and_timestamps(reader: GuardedReader, root: Path) -> None:
    """T17.AC3 (transcripts) and AC4 (mixed-language text intact)."""
    for fact in facts_in(".vtt", "cue"):
        result = converted(reader, root, fact["location"]["file"])
        cue = next(e for e in result.elements if e.provenance["cue"] == fact["location"]["cue"])
        assert cue.provenance["start"] == fact["location"]["start"]
        said = fact.get("decision") or fact.get("action")
        if said:
            assert said.casefold() in cue.text.casefold(), (fact["id"], cue.text)
    quill = "Test data (fictional)/Clients/Quillstone Hotels/Transcripts/Call 2026-09-01.vtt"
    text = converted(reader, root, quill).markdown()
    assert "el informe común se entrega cada lunes" in text


def test_word_paragraphs_and_clauses(reader: GuardedReader, root: Path) -> None:
    """T17.AC3 (Word): the recorded paragraph holds the recorded text."""
    for fact in facts_in(".docx", "paragraph"):
        result = converted(reader, root, fact["location"]["file"])
        element = next(
            e for e in result.elements if e.provenance["paragraph"] == fact["location"]["paragraph"]
        )
        said = fact.get("decision") or fact.get("action") or fact.get("text")
        assert said and said.casefold() in element.text.casefold(), (fact["id"], element.text)


def test_spreadsheet_cells(reader: GuardedReader, root: Path) -> None:
    """T17.AC1 and AC3 (spreadsheets): table cells exact, cell ranges right; AC2 chart data."""
    for fact in [
        f
        for f in FACTS
        if f["task"] == "T11" and f.get("location", {}).get("file", "").endswith(".xlsx")
    ]:
        result = converted(reader, root, fact["location"]["file"])
        row = next(
            e for e in result.elements if e.provenance.get("cell") == fact["location"]["cell"]
        )
        if fact["type"] == "value":
            assert row.text.split(" | ") == [fact["attribute"], *map(str, fact["value"])]
        else:
            assert row.text.split(" | ")[:2] == [fact["label"], str(fact["value"])], row.text


def test_heic_is_decoded_in_memory_with_orientation(
    reader: GuardedReader, root: Path, tmp_path: Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """T17.AC7: one read, no intermediate file, orientation applied."""
    import pillow_heif
    from PIL import Image, ImageOps

    pillow_heif.register_heif_opener()
    heic = next(
        e["path"]
        for e in json.loads((REPO / "data/manifests/fictional.json").read_text())["files"]
        if e["path"].endswith(".heic")
    )
    verified = reader.read(root / heic)
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    jpeg = conv.heic_to_jpeg(verified.data)
    assert set(tmp_path.rglob("*")) == before
    with Image.open(io.BytesIO(verified.data)) as source, Image.open(io.BytesIO(jpeg)) as out:
        assert out.format == "JPEG" and out.size == ImageOps.exif_transpose(source).size


def test_markdown_copy(tmp_path: Path) -> None:
    result = conv.convert(
        VerifiedBytes(relative="Methodology/notes.md", data=b"# Title\n\nBody text.\n", sha256="5")
    )
    path = conv.write_markdown(result, tmp_path)
    assert path == tmp_path / "Methodology/notes.md.md"
    assert "## Title" in path.read_text()


# Slow: Docling --------------------------------------------------------------------


@pytest.fixture(scope="module")
def docling() -> conv.Docling:
    return conv.Docling()


@pytest.mark.slow
def test_pdf_tables_charts_and_pages(
    reader: GuardedReader, root: Path, docling: conv.Docling
) -> None:
    """T17.AC1 (PDF table cells exact), AC2 (chart values recoverable), AC3 (page provenance)."""
    reports = sorted(
        {
            f["location"]["file"]
            for f in FACTS
            if f["task"] == "T11"
            and f.get("location", {}).get("file", "").endswith(".pdf")
            and f["type"] == "chart"
        }
    )
    assert reports
    for report in reports:
        result = converted(reader, root, report, docling)
        for fact in [f for f in FACTS if f.get("location", {}).get("file") == report]:
            page = [
                e for e in result.elements if e.provenance.get("page") == fact["location"]["page"]
            ]
            if fact["type"] == "value":
                cells = [fact["attribute"], *map(str, fact["value"])]
                assert any(e.kind == "table_row" and e.text.split(" | ") == cells for e in page), (
                    report,
                    cells,
                )
            elif fact["type"] == "chart":
                text = " ".join(e.text for e in page)
                assert fact["label"] in text and str(fact["value"]) in text, (report, fact["label"])


@pytest.mark.slow
def test_scanned_page_is_recognised(
    reader: GuardedReader, root: Path, docling: conv.Docling
) -> None:
    """T17.AC4: the scanned acceptance letter, with its order number and Spanish accents."""
    fact = next(f for f in FACTS if f["id"].startswith("talleres-benamar.scanned"))
    result = converted(reader, root, fact["location"]["file"], docling)
    text = result.markdown()
    assert fact["value"] in text
    assert "FICTICIO" in text.upper() or "FICTIONAL" in text.upper()
    assert any(ch in text for ch in "áéíóúñ")


@pytest.mark.slow
def test_receipts_and_whiteboard(reader: GuardedReader, root: Path, docling: conv.Docling) -> None:
    """T17.AC6 and AC8, with every field written to reports/ocr_receipts.csv."""
    from datetime import date

    from company_brain import public_data, receipts
    from company_brain.drive import load_sources
    from company_brain.isolation import real_resources

    rows: list[dict[str, str]] = []

    def record(group: str, file: str, field: str, expected: str, found: receipts.Found) -> bool:
        ok = found.value is not None and found.value == expected
        rows.append(
            {
                "group": group,
                "file": file,
                "field": field,
                "expected": expected,
                "recognised": found.value or "",
                "raw_text": found.raw,
                "result": "exact" if ok else "miss",
            }
        )
        return ok

    def text_of(result: conv.Converted) -> str:
        return "\n".join(e.text for e in result.elements)

    scores: dict[str, list[bool]] = {"clean": [], "photo": [], "cord": []}
    for fact in [f for f in FACTS if f["task"] == "T11R" and f["type"] == "receipt"]:
        spec = next(r for r in image_fixtures.RECEIPTS if fact["files"][0].endswith(r.key + ".png"))
        for file in fact["files"]:
            group = "clean" if file.endswith(".png") else "photo"
            found = receipts.receipt_fields(
                text_of(converted(reader, root, file, docling)), spec.language, spec.vendor
            )
            for field, expected in fact["fields"].items():
                if expected is None:
                    continue  # not applicable (no VAT on this receipt)
                scores[group].append(record(group, file, field, str(expected), found[field]))

    board = next(
        e["path"]
        for e in json.loads((REPO / "data/manifests/fictional.json").read_text())["files"]
        if e.get("role") == "whiteboard"
    )
    text = text_of(converted(reader, root, board, docling))
    owners_and_dates, actions = [], []
    for item in image_fixtures.WHITEBOARD.items:
        action, owner, due = receipts.whiteboard_item(text, item.owner, item.due)
        owners_and_dates += [
            record("whiteboard", board, "owner", item.owner, owner),
            record("whiteboard", board, "due", item.due.isoformat(), due),
        ]
        actions.append(
            record(
                "whiteboard",
                board,
                "action",
                item.action,
                receipts.Found(
                    action.value
                    if action.value and receipts.plain(action.value) == receipts.plain(item.action)
                    else None,
                    action.raw,
                ),
            )
        )

    drive_root = load_sources(REPO / "config" / "sources.toml")["root"]
    drive_reader = GuardedReader(drive_root, Manifest.load(REPO / "data" / "manifests"))
    with real_resources("drive"):
        cord = {
            i.image_id: drive_reader.read(drive_root / public_data.cord_path(i.image_id))
            for i in public_data.CORD_IMAGES
        }
    for fact in public_data.facts():
        result = conv.convert(cord[fact["image_id"]], docling)
        assert result.status == "converted", result.reason
        scores["cord"].append(
            record(
                "cord",
                fact["files"][0],
                "total",
                fact["fields"]["total"],
                receipts.cord_total(text_of(result)),
            )
        )

    receipts.write_ocr_report(REPO / "reports" / "ocr_receipts.csv", rows)
    assert all(scores["clean"]), "clean receipts must be 100% exact"
    assert sum(scores["photo"]) >= 0.8 * len(scores["photo"]), (
        sum(scores["photo"]),
        len(scores["photo"]),
    )
    assert sum(scores["cord"]) >= 4, scores["cord"]
    # T17.AC8 (revision 10): due dates all exact; owners at least 2 of 3, because the owner
    # accepted that handwriting OCR misreads "Lindqvist" as a known limit (24 October).
    dues, owners = owners_and_dates[1::2], owners_and_dates[0::2]
    assert all(dues) and sum(owners) >= 2 and sum(actions) >= 2
    del date
