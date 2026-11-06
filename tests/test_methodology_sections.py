"""T13: the methodology draft covers every listed section in both languages."""

import hashlib
import json
from pathlib import Path

from docx import Document

from company_brain import methodology

REPO = Path(__file__).resolve().parents[1]
SECTIONS = {
    "en": [
        "Value-based fees",
        "Conceptual agreement",
        "Objectives",
        "Measures of success",
        "Value",
        "Options",
        "Phrases to use and avoid",
    ],
    "es": [
        "Honorarios basados en el valor",
        "Acuerdo conceptual",
        "Objetivos",
        "Medidas de éxito",
        "Valor",
        "Opciones",
        "Frases que usar y evitar",
    ],
}


def test_every_section_in_both_languages(tmp_path: Path) -> None:
    """T13.AC1: every section heading, in order, each with real content."""
    built = methodology.build(tmp_path)
    assert {entry["language"] for entry in built} == {"en", "es"}
    for entry in built:
        path = tmp_path / entry["path"][len(methodology.PREFIX) + 1 :]
        paragraphs = Document(str(path)).paragraphs
        headings = [p.text for p in paragraphs if p.style.name == "Heading 2"]
        assert headings == SECTIONS[entry["language"]]
        body: dict[str, int] = {}
        current = ""
        for paragraph in paragraphs:
            if paragraph.style.name == "Heading 2":
                current = paragraph.text
            elif current and paragraph.text.strip():
                body[current] = body.get(current, 0) + len(paragraph.text.split())
        for heading in headings:
            assert body.get(heading, 0) >= 60, (entry["language"], heading)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]


def test_draft_is_listed_and_reproducible(tmp_path: Path) -> None:
    built = methodology.build(tmp_path / "a")
    assert methodology.build(tmp_path / "b") == built
    manifest = json.loads((REPO / "data" / "manifests" / "fictional.json").read_text())
    assert [e for e in manifest["files"] if e["task"] == methodology.TASK] == built
