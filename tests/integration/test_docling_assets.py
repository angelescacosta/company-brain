"""T3.AC3 and T3.AC4: Docling and Apple Vision OCR work from local assets only.

Conversions run in a child process that cannot reach the internet, with
Hugging Face offline mode set. Fixtures: `tests/fixtures/t3/` (fictional,
made by `scripts/make_t3_fixtures.py`).
"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "t3"

CONVERT = """
import json, sys
from pathlib import Path
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import OcrMacOptions, PdfPipelineOptions
from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

options = PdfPipelineOptions(
    do_ocr=True,
    ocr_options=OcrMacOptions(force_full_page_ocr={force_ocr}),
    do_table_structure=True,
    do_picture_classification=True,
    generate_picture_images=True,
    enable_remote_services=False,
    artifacts_path=str(Path("~/.cache/docling/models").expanduser()),
)
converter = DocumentConverter(format_options={{
    InputFormat.PDF: PdfFormatOption(pipeline_options=options),
    InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
}})
document = converter.convert(Path({path!r})).document
tables = [[[cell.text for cell in row] for row in table.data.grid] for table in document.tables]
pictures = [
    [c.class_name for a in p.annotations if hasattr(a, "predicted_classes")
     for c in a.predicted_classes[:1]]
    for p in document.pictures
]
print(json.dumps({{"markdown": document.export_to_markdown(), "tables": tables,
                  "pictures": pictures}}))
"""


def convert(run_offline, name: str, force_ocr: bool = False) -> dict:  # type: ignore[no-untyped-def,type-arg]
    result = run_offline(CONVERT.format(path=str(FIXTURES / name), force_ocr=force_ocr))
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_table(run_offline) -> None:  # type: ignore[no-untyped-def]
    output = convert(run_offline, "table.pdf")
    assert output["tables"], "no table found"
    cells = {cell for row in output["tables"][0] for cell in row}
    assert {"Q2 2026", "48,900", "Revenue (EUR)", "11"} <= cells


def test_chart(run_offline) -> None:  # type: ignore[no-untyped-def]
    output = convert(run_offline, "chart.pdf")
    assert output["pictures"], "no picture found"
    labels = {label for picture in output["pictures"] for label in picture}
    assert any("chart" in label.lower() for label in labels), labels


def test_scanned_page(run_offline) -> None:  # type: ignore[no-untyped-def]
    text = convert(run_offline, "scanned.pdf", force_ocr=True)["markdown"]
    assert "FICTIONAL" in text
    assert "12 January 2027" in text
    assert "Carla Ortiz" in text


def test_receipt_photo(run_offline) -> None:  # type: ignore[no-untyped-def]
    text = convert(run_offline, "receipt_photo.jpg", force_ocr=True)["markdown"]
    assert "10.70" in text
    assert "Brisa" in text


def test_ocrmac_reads_receipt_offline(run_offline) -> None:  # type: ignore[no-untyped-def]
    result = run_offline(
        f"""
        from ocrmac.ocrmac import OCR
        lines = [text for text, _, _ in OCR({str(FIXTURES / "receipt_photo.jpg")!r}).recognize()]
        joined = " ".join(lines)
        for expected in ("FICTIONAL", "Cafe Brisa Norte", "14/09/2026", "10.70", "0.97"):
            assert expected in joined, (expected, lines)
        print("OK")
        """
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith("OK")
