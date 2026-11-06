"""T18.AC4 and dispatch: every chunk has a label, a parent link and source spans."""

from pathlib import Path

import pytest

from company_brain import corpus
from company_brain.chunkers import DEFAULT_BUDGET, chunk_document, count_tokens
from company_brain.content_guard import GuardedReader, Manifest
from company_brain.convert import convert

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    root = tmp_path_factory.mktemp("root")
    result = corpus.build(root / corpus.PREFIX)
    return root, result, GuardedReader(root, Manifest.load(REPO / "data" / "manifests"))


def test_labels_parents_and_spans(built) -> None:  # type: ignore[no-untyped-def]
    root, result, reader = built
    kinds = set()
    for entry in result.files:
        if not entry["path"].endswith((".vtt", ".docx", ".xlsx")):
            continue  # PDFs need Docling; covered by the slow conversion tests
        converted = convert(reader.read(root / entry["path"]))
        chunks = chunk_document(converted, entry["role"], DEFAULT_BUDGET, entry["client"])
        assert chunks, entry["path"]
        for chunk in chunks:
            kinds.add(chunk.kind)
            assert chunk.label.startswith(entry["client"])
            assert Path(entry["path"]).name in chunk.label
            assert chunk.parent.startswith(entry["path"])
            assert chunk.spans and all(isinstance(s, dict) and s for s in chunk.spans)
            assert count_tokens(chunk.full()) <= DEFAULT_BUDGET
    assert {"transcript", "clause"} <= kinds


def test_fee_clause_keeps_its_paragraph(built) -> None:  # type: ignore[no-untyped-def]
    root, result, reader = built
    path = (
        "Test data (fictional)/Clients/Harrowgate Freight/Contracts/"
        "Consulting agreement (signed).docx"
    )
    entry = next(e for e in result.files if e["path"] == path)
    chunks = chunk_document(convert(reader.read(root / path)), entry["role"], 300, entry["client"])
    fee = [c for c in chunks if "48,000" in c.text]
    assert fee and any(s.get("paragraph") == 5 for s in fee[0].spans)
