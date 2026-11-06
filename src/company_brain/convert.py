"""Document conversion with provenance (T17).

Input is always the guarded reader's verified bytes (T9); nothing is reopened
and no intermediate file is written. The content signature is checked against
the file extension first, and a mismatch is marked failed without converting.

Docling (local models only) converts PDFs and slides, with Apple Vision OCR
(ocrmac) for scanned pages and picture classification on. Image files are read
with Apple Vision directly, and their words are put back into lines by
position, because Docling's layout step merges receipt lines into blocks and
separates labels from amounts (D33); the picture-description model runs only
on images with little recognised text.
Word files, spreadsheets and transcripts are read directly (python-docx,
openpyxl and a WebVTT parser), because those give exact paragraph, cell and
cue provenance (D33). Chart values in the test reports are printed on the page
or held in cells, so no chart-reading model is loaded.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from company_brain.content_guard import VerifiedBytes

ARTIFACTS = Path("~/.cache/docling/models").expanduser()
LITTLE_TEXT = 20  # characters of recognised text below which an image gets a description
EXPECTED = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".heic": "heic",
    ".vtt": "vtt",
    ".md": "text",
    ".txt": "text",
}
_ZIP_KINDS = {
    "word/document.xml": "docx",
    "xl/workbook.xml": "xlsx",
    "ppt/presentation.xml": "pptx",
}


@dataclass(frozen=True)
class Element:
    kind: str  # heading, paragraph, table_row, cue, text, picture
    text: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class Converted:
    relative: str
    sha256: str
    status: str  # "converted" or "failed"
    reason: str = ""
    elements: tuple[Element, ...] = field(default_factory=tuple)

    def markdown(self) -> str:
        lines = [f"<!-- source: {self.relative} -->"]
        for element in self.elements:
            if element.kind == "heading":
                lines.append(f"## {element.text}")
            elif element.kind == "table_row":
                lines.append(f"| {element.text} |")
                continue
            elif element.kind == "cue":
                lines.append(f"[{element.provenance['start']}] {element.text}")
            else:
                lines.append(element.text)
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


class ConversionError(Exception):
    """The file could not be converted; the reason names the kind of problem only."""


def sniff(data: bytes) -> str | None:
    """The file type the bytes really are, from their signature."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[4:8] == b"ftyp" and data[8:12] in {b"heic", b"heix", b"mif1", b"msf1", b"heim"}:
        return "heic"
    if data.startswith(b"PK\x03\x04"):
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            return None
        return next((kind for name, kind in _ZIP_KINDS.items() if name in names), None)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    return "vtt" if text.lstrip().startswith("WEBVTT") else "text"


# Transcripts, Word and spreadsheets -------------------------------------------------

_CUE_TIME = re.compile(r"(\d{2}:\d{2}:\d{2}\.\d{3}) --> (\d{2}:\d{2}:\d{2}\.\d{3})")
_SPEAKER = re.compile(r"^<v ([^>]+)>(.*)$", re.S)


def vtt_elements(data: bytes) -> list[Element]:
    blocks = re.split(r"\n\s*\n", data.decode("utf-8-sig").replace("\r\n", "\n").strip())
    if not blocks or not blocks[0].startswith("WEBVTT"):
        raise ConversionError("not a WebVTT transcript")
    elements, cue = [], 0
    for block in blocks[1:]:
        lines = block.split("\n")
        timing = next((i for i, line in enumerate(lines) if _CUE_TIME.search(line)), None)
        if timing is None:
            continue  # NOTE or STYLE block
        cue += 1
        start, end = _CUE_TIME.search(lines[timing]).groups()  # type: ignore[union-attr]
        text = " ".join(lines[timing + 1 :]).strip()
        provenance: dict[str, Any] = {"cue": cue, "start": start, "end": end}
        if match := _SPEAKER.match(text):
            provenance["speaker"] = match[1]
            text = f"{match[1]}: {match[2].strip()}"
        elements.append(Element("cue", text, provenance))
    return elements


def docx_elements(data: bytes) -> list[Element]:
    from docx import Document

    document = Document(io.BytesIO(data))
    elements = []
    for index, paragraph in enumerate(document.paragraphs):
        text = paragraph.text.strip()
        if not text:
            continue
        style = paragraph.style.name if paragraph.style is not None else ""
        kind = "heading" if style.startswith(("Heading", "Title")) else "paragraph"
        elements.append(Element(kind, text, {"paragraph": index}))
    for number, table in enumerate(document.tables, start=1):
        for row_number, row in enumerate(table.rows, start=1):
            cells = [cell.text.strip() for cell in row.cells]
            elements.append(
                Element("table_row", " | ".join(cells), {"table": number, "row": row_number})
            )
    return elements


def xlsx_elements(data: bytes) -> list[Element]:
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    book = load_workbook(io.BytesIO(data), data_only=True)
    elements = []
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            filled = [cell for cell in row if cell.value is not None and str(cell.value).strip()]
            if not filled:
                continue
            first, last = filled[0].column, filled[-1].column
            number = filled[0].row
            span = f"{get_column_letter(first)}{number}"
            if last != first:
                span += f":{get_column_letter(last)}{number}"
            values = [
                "" if cell.value is None else str(cell.value).strip()
                for cell in row[first - 1 : last]
            ]
            elements.append(
                Element(
                    "table_row" if len(filled) > 1 else "paragraph",
                    " | ".join(values),
                    {"sheet": sheet.title, "cell": f"{sheet.title}!{span}"},
                )
            )
    return elements


def text_elements(data: bytes) -> list[Element]:
    text = data.decode("utf-8-sig")
    elements, line = [], 1
    for block in re.split(r"(\n\s*\n)", text):
        if block.strip() and not re.fullmatch(r"\n\s*\n", block):
            kind = "heading" if block.lstrip().startswith("#") else "paragraph"
            elements.append(Element(kind, block.strip().lstrip("# ").strip(), {"line": line}))
        line += block.count("\n")
    return elements


# Docling: PDFs, slides and images -----------------------------------------------------


def heic_to_jpeg(data: bytes) -> bytes:
    """Decode HEIC in memory, apply its orientation, and re-encode as JPEG (no file written)."""
    import pillow_heif
    from PIL import Image, ImageOps

    pillow_heif.register_heif_opener()
    with Image.open(io.BytesIO(data)) as image:
        upright = ImageOps.exif_transpose(image).convert("RGB")
    out = io.BytesIO()
    upright.save(out, format="JPEG", quality=95)
    return out.getvalue()


def _pdf_needs_ocr(data: bytes) -> bool:
    """True when no page has a text layer (a scanned PDF). Encrypted PDFs raise."""
    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        raise ConversionError("encrypted or unreadable PDF") from error
    try:
        return all(
            len(document[i].get_textpage().get_text_range().strip()) < LITTLE_TEXT
            for i in range(len(document))
        )
    finally:
        document.close()


class Docling:
    """Docling converters built once and reused (model loading takes seconds)."""

    def __init__(self, artifacts: Path = ARTIFACTS) -> None:
        self.artifacts = artifacts
        self._converters: dict[tuple[bool, bool], Any] = {}

    def _converter(self, force_ocr: bool, describe: bool) -> Any:
        key = (force_ocr, describe)
        if key not in self._converters:
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import (
                OcrMacOptions,
                PdfPipelineOptions,
                smolvlm_picture_description,
            )
            from docling.document_converter import (
                DocumentConverter,
                ImageFormatOption,
                PdfFormatOption,
            )

            options = PdfPipelineOptions(
                do_ocr=True,
                ocr_options=OcrMacOptions.model_validate(
                    {"force_full_page_ocr": force_ocr, "lang": ["en-US", "es-ES"]}
                ),
                do_table_structure=True,
                do_picture_classification=True,
                do_picture_description=describe,
                picture_description_options=smolvlm_picture_description,
                do_chart_extraction=False,
                generate_picture_images=describe,
                enable_remote_services=False,
                artifacts_path=str(self.artifacts),
            )
            self._converters[key] = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=options),
                    InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
                }
            )
        return self._converters[key]

    def elements(
        self, name: str, data: bytes, force_ocr: bool, describe: bool = False
    ) -> list[Element]:
        from docling.datamodel.base_models import DocumentStream

        stream = DocumentStream(name=name, stream=io.BytesIO(data))
        result = self._converter(force_ocr, describe).convert(stream, raises_on_error=False)
        if result.document is None or str(result.status).lower().endswith("failure"):
            raise ConversionError("the converter could not read this file")
        return list(_docling_elements(result.document))


def _docling_elements(document: Any) -> Iterable[Element]:
    from docling_core.types.doc.document import (  # type: ignore[attr-defined]
        PictureItem,
        TableItem,
        TextItem,
    )

    tables = 0
    # Text inside a picture (chart labels and values) is kept, not dropped with the image.
    for item, _level in document.iterate_items(traverse_pictures=True):
        page = item.prov[0].page_no if getattr(item, "prov", None) else None
        where: dict[str, Any] = {"page": page} if page is not None else {}
        if isinstance(item, TableItem):
            tables += 1
            for row_number, row in enumerate(item.data.grid, start=1):
                cells = [cell.text.strip() for cell in row]
                yield Element(
                    "table_row", " | ".join(cells), {**where, "table": tables, "row": row_number}
                )
        elif isinstance(item, PictureItem):
            labels = []
            for annotation in getattr(item, "annotations", []) or []:
                if hasattr(annotation, "predicted_classes") and annotation.predicted_classes:
                    labels.append(f"[{annotation.predicted_classes[0].class_name}]")
                elif getattr(annotation, "text", None):
                    labels.append(annotation.text.strip())
            if labels:
                yield Element("picture", " ".join(labels), where)
        elif isinstance(item, TextItem) and item.text.strip():
            label = str(getattr(item, "label", "")).lower()
            kind = "heading" if "header" in label or "title" in label else "text"
            yield Element(kind, item.text.strip(), where)


def ocr_lines(data: bytes) -> list[Element]:
    """Apple Vision OCR on in-memory image bytes, words regrouped into lines top to bottom."""
    from ocrmac import ocrmac  # type: ignore[import-untyped]
    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as image:
        upright = ImageOps.exif_transpose(image).convert("RGB")
    words = ocrmac.OCR(upright, language_preference=["en-US", "es-ES"]).recognize()
    # Vision boxes are (x, y, width, height), normalised, origin at the bottom left.
    boxes = [(text, x, y + h / 2, h) for text, _confidence, (x, y, _w, h) in words if text.strip()]
    return [
        Element("text", " ".join(word for word, *_ in line), {"line": number})
        for number, line in enumerate(_rows(boxes), start=1)
    ]


def _rows(
    boxes: list[tuple[str, float, float, float]],
) -> list[list[tuple[str, float, float, float]]]:
    """Group word boxes into rows, allowing for a tilted photo.

    Tries slopes from -0.15 to 0.15 and keeps the one that gives the fewest rows
    (ties: the flattest), so a label and its amount on one tilted row stay together.
    """

    def group(slope: float) -> list[list[tuple[str, float, float, float]]]:
        rows: list[list[tuple[str, float, float, float]]] = []
        level: list[float] = []
        for box in sorted(boxes, key=lambda b: -(b[2] - slope * b[1])):
            height = box[2] - slope * box[1]
            if rows and abs(level[-1] - height) < 0.5 * box[3]:
                rows[-1].append(box)
            else:
                rows.append([box])
                level.append(height)
        return [sorted(row, key=lambda b: b[1]) for row in rows]

    slopes = sorted((s / 100 for s in range(-15, 16)), key=abs)
    return min((group(s) for s in slopes), key=len) if boxes else []


# Entry point --------------------------------------------------------------------------


def convert(verified: VerifiedBytes, docling: Docling | None = None) -> Converted:
    """Convert one verified file; any failure is recorded and never raised."""
    suffix = PurePosixPath(verified.relative).suffix.lower()
    expected = EXPECTED.get(suffix)

    def failed(reason: str) -> Converted:
        return Converted(verified.relative, verified.sha256, "failed", reason)

    if expected is None:
        return failed(f"unsupported file type {suffix}")
    actual = sniff(verified.data)
    if actual != expected:
        return failed(f"content is {actual or 'unknown'}, not {expected}")
    try:
        elements = _convert(expected, verified, docling)
    except ConversionError as error:
        return failed(str(error))
    except Exception as error:  # noqa: BLE001 - any converter crash is recorded, the run goes on
        return failed(f"conversion error ({type(error).__name__})")
    if not elements:
        return failed("no readable content")
    return Converted(verified.relative, verified.sha256, "converted", elements=tuple(elements))


def _convert(kind: str, verified: VerifiedBytes, docling: Docling | None) -> list[Element]:
    data, name = verified.data, PurePosixPath(verified.relative).name
    if kind == "vtt":
        return vtt_elements(data)
    if kind == "docx":
        return docx_elements(data)
    if kind == "xlsx":
        return xlsx_elements(data)
    if kind == "text":
        return text_elements(data)
    engine = docling or Docling()  # built lazily: image files only need it for descriptions
    if kind == "pdf":
        return engine.elements(name, data, force_ocr=_pdf_needs_ocr(data))
    if kind == "pptx":
        return engine.elements(name, data, force_ocr=False)
    if kind == "heic":
        data, name = heic_to_jpeg(data), PurePosixPath(name).stem + ".jpg"
    elements = ocr_lines(data)
    if sum(len(e.text) for e in elements) < LITTLE_TEXT:
        elements += engine.elements(name, data, force_ocr=True, describe=True)
    # An image has one "page"; provenance says so, and the file name stays the original.
    return [Element(e.kind, e.text, {**e.provenance, "image": True}) for e in elements]


def write_markdown(converted: Converted, folder: Path) -> Path:
    """Write the Markdown copy under `folder`, mirroring the root-relative path."""
    target = folder / f"{converted.relative}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(converted.markdown(), encoding="utf-8")
    return target
