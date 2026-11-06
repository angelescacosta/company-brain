"""Render the small fictional fixture set for T3 (table, chart, scanned page, receipt photo).

Usage: uv run python scripts/make_t3_fixtures.py [output folder]
Deterministic: fixed seeds and Pillow's built-in font.
"""

from __future__ import annotations

import io
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FIXED_DATE = datetime(2026, 10, 20, tzinfo=UTC)
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("tests/fixtures/t3")

TABLE = [
    ("Quarter", "Revenue (EUR)", "Clients"),
    ("Q1 2026", "41,200", "7"),
    ("Q2 2026", "48,900", "9"),
    ("Q3 2026", "52,350", "11"),
]
CHART = [("North", 120), ("South", 85), ("East", 150), ("West", 60)]
SCANNED_LINES = [
    "FICTIONAL - Meridian Lumen S.L.",
    "Minutes of the steering meeting",
    "Decision: the pilot starts on 12 January 2027.",
    "Owner of the rollout plan: Carla Ortiz.",
]
RECEIPT_LINES = [
    "FICTIONAL RECEIPT",
    "Cafe Brisa Norte",
    "Date: 14/09/2026",
    "Coffee            3.20",
    "Sandwich          7.50",
    "VAT 10%           0.97",
    "TOTAL EUR        10.70",
]


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def pdf() -> FPDF:
    document = FPDF(format="A4")
    document.set_creation_date(FIXED_DATE)
    document.set_producer("company-brain fixtures")
    document.add_page()
    document.set_font("Helvetica", size=12)
    return document


def table_pdf() -> bytes:
    document = pdf()
    document.cell(
        0, 10, "FICTIONAL - Quarterly results of Meridian Lumen S.L.", new_x="LMARGIN", new_y="NEXT"
    )
    with document.table() as table:
        for row in TABLE:
            cells = table.row()
            for value in row:
                cells.cell(value)
    return bytes(document.output())


def chart_png() -> bytes:
    image = Image.new("RGB", (900, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 20), "FICTIONAL - Projects per region, 2026", fill="black", font=font(30))
    base, width, gap = 520, 140, 60
    for index, (label, value) in enumerate(CHART):
        x = 80 + index * (width + gap)
        draw.rectangle([x, base - value * 2.5, x + width, base], fill=(60, 110, 170))
        draw.text((x + 40, base - value * 2.5 - 40), str(value), fill="black", font=font(28))
        draw.text((x + 25, base + 15), label, fill="black", font=font(28))
    draw.line([60, base, 860, base], fill="black", width=3)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def chart_pdf(png: bytes) -> bytes:
    document = pdf()
    document.cell(0, 10, "FICTIONAL - Regional activity report", new_x="LMARGIN", new_y="NEXT")
    document.image(io.BytesIO(png), w=170)
    return bytes(document.output())


def scanned_pdf() -> bytes:
    rng = random.Random(3)  # noqa: S311 - fixed seed for repeatable fixture noise
    page = Image.new("L", (1240, 1754), 245)
    draw = ImageDraw.Draw(page)
    for index, line in enumerate(SCANNED_LINES):
        draw.text((110, 160 + index * 80), line, fill=20, font=font(40))
    for _ in range(4000):
        x, y = rng.randrange(1240), rng.randrange(1754)
        page.putpixel((x, y), rng.randrange(150, 230))
    page = page.rotate(0.8, fillcolor=245).filter(ImageFilter.GaussianBlur(0.6))
    buffer = io.BytesIO()
    page.save(buffer, format="PNG")
    document = FPDF(format="A4")
    document.set_creation_date(FIXED_DATE)
    document.set_producer("company-brain fixtures")
    document.add_page()
    document.image(io.BytesIO(buffer.getvalue()), x=0, y=0, w=210, h=297)
    return bytes(document.output())


def receipt_photo() -> bytes:
    receipt = Image.new("RGB", (560, 760), (250, 248, 240))
    draw = ImageDraw.Draw(receipt)
    for index, line in enumerate(RECEIPT_LINES):
        draw.text((40, 50 + index * 90), line, fill=(25, 25, 25), font=font(36))
    photo = Image.new("RGB", (900, 1100), (90, 80, 70))
    photo.paste(receipt.rotate(4, expand=True, fillcolor=(90, 80, 70)), (150, 140))
    light = Image.linear_gradient("L").resize(photo.size).point(lambda v: 170 + v // 3)
    photo = Image.composite(photo, Image.new("RGB", photo.size, (40, 35, 30)), light)
    photo = photo.filter(ImageFilter.GaussianBlur(1.0))
    buffer = io.BytesIO()
    photo.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    png = chart_png()
    outputs = {
        "table.pdf": table_pdf(),
        "chart.pdf": chart_pdf(png),
        "scanned.pdf": scanned_pdf(),
        "receipt_photo.jpg": receipt_photo(),
    }
    for name, data in outputs.items():
        (OUT / name).write_bytes(data)
        print(name, len(data))


if __name__ == "__main__":
    main()
