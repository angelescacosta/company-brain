"""T31: export of a checked summary or draft to Word and PDF.

PBT for the path guard: for any generated path, export refuses Drive, iCloud,
Dropbox, OneDrive and the synced Desktop and Documents folders, including
through `..` and symbolic links (protects client text from leaving the Mac).
"""

import io
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from docx import Document
from hypothesis import given
from hypothesis import strategies as st

from company_brain import export
from company_brain.guardrails import Gated

HOME = Path.home()
SYNCED = [
    HOME / "Library" / "CloudStorage" / "GoogleDrive-x@y.com" / "My Drive",
    HOME / "Library" / "Mobile Documents" / "com~apple~CloudDocs",
    HOME / "Library" / "CloudStorage" / "Dropbox",
    HOME / "Library" / "CloudStorage" / "OneDrive-Personal",
    HOME / "Dropbox",
    HOME / "Google Drive",
    HOME / "Desktop",
    HOME / "Documents",
]
names = st.lists(st.sampled_from(["a", "Exports", "..", "Clients", "x y", "Ñandú"]), max_size=4)


@given(st.sampled_from(SYNCED), names)
def test_synced_locations_are_refused(base: Path, parts: list[str]) -> None:
    candidate = base.joinpath(*parts)
    resolved = Path(export.os.path.normpath(candidate))  # type: ignore[attr-defined]
    inside = any(resolved == s or s in resolved.parents for s in SYNCED)
    if inside:
        with pytest.raises(export.UnsafeExportPath):
            export.check_export_dir(candidate)


@given(names)
def test_the_default_folder_is_accepted(parts: list[str]) -> None:
    folder = HOME / "CompanyBrainExports"
    candidate = folder.joinpath(*[p for p in parts if p != ".."])
    export.check_export_dir(candidate)


def test_a_link_into_a_synced_folder_is_refused(tmp_path: Path) -> None:
    link = tmp_path / "exports"
    link.symlink_to(HOME / "Documents")
    with pytest.raises(export.UnsafeExportPath):
        export.check_export_dir(link)


GATED = Gated(
    text="Decision: the first phase covers Solihull and Dudley. [1]\n"
    "Action: export twelve months of booking data "
    "(owner Hannah Greaves, due 4 September 2026). [2]",
    sources=[
        {
            "n": 1,
            "name": "Call 2026-08-27.vtt",
            "location": "at 00:00:39",
            "file": "x",
            "excerpt": "e",
        },
        {
            "n": 2,
            "name": "Call 2026-08-27.vtt",
            "location": "at 00:00:53",
            "file": "x",
            "excerpt": "e",
        },
    ],
)


def test_both_files_open_with_text_headings_and_citations(tmp_path: Path) -> None:
    """T31.AC1."""
    word, pdf = export.export(GATED, "Summary of Call 2026-08-27.vtt", tmp_path, check=False)
    document = Document(str(word))
    headings = [p.text for p in document.paragraphs if p.style.name.startswith("Heading")]
    assert headings[0] == "Summary of Call 2026-08-27.vtt" and "Sources" in headings
    text = "\n".join(p.text for p in document.paragraphs)
    assert "Solihull and Dudley. [1]" in text and "[2] Call 2026-08-27.vtt, at 00:00:53" in text
    pages = pdfium.PdfDocument(io.BytesIO(pdf.read_bytes()))
    pdf_text = " ".join(pages[i].get_textpage().get_text_range() for i in range(len(pages)))
    assert "Solihull and Dudley. [1]" in " ".join(pdf_text.split())
    assert "Summary of Call 2026-08-27.vtt" in pdf_text and "Sources" in pdf_text
    assert word.parent == pdf.parent == tmp_path


def test_only_gated_output_can_be_exported(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        export.export("plain text", "x", tmp_path, check=False)  # type: ignore[arg-type]


def test_exports_record_their_client(tmp_path: Path) -> None:
    """T42: every export is listed with its owner, so deleting a client removes its files."""
    import json

    word, pdf = export.export(GATED, "Summary A", tmp_path, check=False, owner="fictional:A")
    export.export(GATED, "Summary B", tmp_path, check=False, owner="fictional:B")
    index = json.loads((tmp_path / "exports.json").read_text())
    assert index[word.name] == index[pdf.name] == "fictional:A"
    assert sorted(set(index.values())) == ["fictional:A", "fictional:B"]
