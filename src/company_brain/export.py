"""Export a checked summary or draft to Word and PDF (T31).

Only output that has passed the gate (a `Gated` object, T25) can be exported.
Files go to `~/CompanyBrainExports`, which must never be inside Drive, iCloud,
Dropbox, OneDrive, or the Desktop and Documents folders that iCloud can sync;
the check resolves `..` and symbolic links first.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from docx import Document
from fpdf import FPDF

from company_brain.guardrails import Gated

FONT = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "DejaVuSans.ttf"
_SYNCED_NAMES = re.compile(r"google ?drive|googledrive|dropbox|onedrive|icloud|clouddocs", re.I)


class UnsafeExportPath(ValueError):
    """The export folder is, or is inside, a synced location."""


def _synced_roots() -> list[Path]:
    home = Path.home()
    return [
        home / "Library" / "CloudStorage",
        home / "Library" / "Mobile Documents",
        home / "Desktop",
        home / "Documents",
    ]


def check_export_dir(folder: Path) -> Path:
    resolved = Path(os.path.realpath(folder))
    for root in _synced_roots():
        real_root = Path(os.path.realpath(root))
        if resolved == real_root or real_root in resolved.parents:
            raise UnsafeExportPath(f"{folder} is in a synced folder ({root.name})")
    if any(_SYNCED_NAMES.search(part) for part in resolved.parts):
        raise UnsafeExportPath(f"{folder} looks like a synced folder")
    return resolved


def _safe_name(title: str) -> str:
    stem = re.sub(r"[^\w .-]+", " ", title).strip() or "Company Brain"
    return f"{stem[:80]} {datetime.now().strftime('%Y-%m-%d %H%M%S')}"


def _source_lines(gated: Gated) -> list[str]:
    lines = []
    for source in gated.sources:
        where = f", {source['location']}" if source.get("location") else ""
        lines.append(f"[{source['n']}] {source['name']}{where}")
    return lines


def export(
    gated: Gated, title: str, folder: Path, check: bool = True, owner: str | None = None
) -> tuple[Path, Path]:
    """Write `<title> <time>.docx` and `.pdf`; returns both paths."""
    if not isinstance(gated, Gated):
        raise TypeError("only output that passed the output gate can be exported")
    target = check_export_dir(folder) if check else folder
    target.mkdir(parents=True, exist_ok=True)
    base = target / _safe_name(title)
    sources = _source_lines(gated)

    document = Document()
    document.add_heading(title, level=1)
    for block in gated.text.split("\n"):
        if block.strip():
            document.add_paragraph(block)
    if sources:
        document.add_heading("Sources", level=2)
        for line in sources:
            document.add_paragraph(line)
    word = base.with_suffix(".docx")
    document.save(str(word))

    pdf = FPDF()
    pdf.add_font("DejaVu", "", str(FONT))
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("DejaVu", size=16)
    pdf.multi_cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("DejaVu", size=11)
    for block in gated.text.split("\n"):
        if block.strip():
            pdf.multi_cell(0, 6, block, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
    if sources:
        pdf.ln(3)
        pdf.set_font("DejaVu", size=13)
        pdf.multi_cell(0, 8, "Sources", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", size=10)
        for line in sources:
            pdf.multi_cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    pdf_path = base.with_suffix(".pdf")
    pdf.output(str(pdf_path))
    if owner is not None:  # ownership, so deleting a client removes its exports (T42)
        index_path = target / "exports.json"
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        index.update({word.name: owner, pdf_path.name: owner})
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return word, pdf_path
