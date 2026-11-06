"""Receipt and whiteboard image fixtures for the fictional clients (T11R).

Eight fictional receipts (four English, four Spanish), each drawn as a clean PNG
and as a simulated phone photo (rotation, perspective, uneven light, a crease,
blur) saved as JPEG; two photos are also saved as HEIC. One whiteboard photo
carries three action items in a handwriting-style font. Every image says
FICTIONAL. Seeds and fonts are pinned, so rendering twice gives identical files.
"""

from __future__ import annotations

import hashlib
import io
import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pillow_heif
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from company_brain.corpus import PREFIX, Built, merge_records, slug
from company_brain.corpus_data import Action

TASK = "T11R"
FONTS = Path(__file__).resolve().parents[2] / "assets" / "fonts"
RECEIPT_FONT = FONTS / "DejaVuSansMono.ttf"
HAND_FONT = FONTS / "PatrickHand-Regular.ttf"
FOLDER = "Receipts"


@dataclass(frozen=True)
class Receipt:
    key: str
    client: str
    language: str
    vendor: str
    address: str
    day: date
    date_text: str
    items: tuple[tuple[str, str], ...]  # description, amount (subtotal is their sum)
    vat_rate: str  # printed label, empty when VAT does not apply
    vat: str | None
    total: str
    currency: str
    heic: bool = False


@dataclass(frozen=True)
class Whiteboard:
    key: str
    client: str
    title: str
    items: tuple[Action, ...]
    due_text: tuple[str, ...]


RECEIPTS: tuple[Receipt, ...] = (
    Receipt(
        "Receipt 1 taxi",
        "Harrowgate Freight",
        "en",
        "Larkspur Cabs",
        "14 Wellgate Row, Leeds",
        date(2026, 9, 28),
        "28/09/2026",
        (("Fare, station to depot", "21.40"), ("Waiting time", "2.00")),
        "",
        None,
        "23.40",
        "GBP",
    ),
    Receipt(
        "Receipt 2 coffee",
        "Harrowgate Freight",
        "en",
        "Copperkettle Coffee",
        "3 Saddler Yard, Leeds",
        date(2026, 10, 6),
        "06 Oct 2026",
        (("Flat white x2", "6.50"), ("Cheese sandwich x2", "5.75")),
        "VAT 20%",
        "2.45",
        "14.70",
        "GBP",
        heic=True,
    ),
    Receipt(
        "Receipt 3 printing",
        "Pellmore Dental Group",
        "en",
        "Brindlewood Print",
        "88 Foundry Lane, Solihull",
        date(2026, 10, 13),
        "13/10/2026",
        (("Workshop handouts x60", "54.00"), ("Laminated posters x6", "18.00")),
        "VAT 20%",
        "14.40",
        "86.40",
        "GBP",
    ),
    Receipt(
        "Receipt 4 rail",
        "Quillstone Hotels",
        "en",
        "Minsterline Rail Tours",
        "Platform 2 kiosk, York",
        date(2026, 10, 20),
        "20 Oct 2026",
        (("Return York to Harrogate", "41.90"),),
        "VAT 0%",
        "0.00",
        "41.90",
        "GBP",
    ),
    Receipt(
        "Recibo 5 comida",
        "Conservas Arrieta",
        "es",
        "Asador Itxasbide",
        "Kalea Nagusia 12, Bermeo",
        date(2026, 10, 15),
        "15/10/2026",
        (("Menú del día x2", "47.00"), ("Agua y cafés", "12.00")),
        "IVA 10%",
        "5.90",
        "64.90",
        "EUR",
    ),
    Receipt(
        "Recibo 6 combustible",
        "Conservas Arrieta",
        "es",
        "Estación de servicio Gaztelu",
        "Carretera BI-635 km 3",
        date(2026, 10, 15),
        "15-10-2026",
        (("Gasóleo 32,90 l", "43.22"),),
        "IVA 21%",
        "9.08",
        "52.30",
        "EUR",
        heic=True,
    ),
    Receipt(
        "Recibo 7 papeleria",
        "Talleres Benamar",
        "es",
        "Papelería Lirio Azul",
        "Calle Cerrojo 7, Málaga",
        date(2026, 10, 19),
        "19/10/2026",
        (("Carpetas x10", "9.00"), ("Rotuladores x6", "6.00")),
        "IVA 21%",
        "3.15",
        "18.15",
        "EUR",
    ),
    Receipt(
        "Recibo 8 aparcamiento",
        "Clinica Veterinaria Olmedo",
        "es",
        "Aparcamiento Trébol",
        "Plaza del Ochavo 2, Valladolid",
        date(2026, 10, 14),
        "14/10/2026",
        (("Estancia 3 h 10 min", "6.00"),),
        "IVA 21%",
        "1.26",
        "7.26",
        "EUR",
    ),
)

WHITEBOARD = Whiteboard(
    key="Whiteboard Hull rollout",
    client="Harrowgate Freight",
    title="Hull rollout - actions",
    items=(
        Action("Marcus Bell", "Update the Hull depot map", date(2026, 10, 30)),
        Action("Dana Whitcombe", "Book the driver briefing room", date(2026, 11, 2)),
        Action("Sofía Lindqvist", "Print the handover checklists", date(2026, 10, 29)),
    ),
    due_text=("30 Oct 2026", "2 Nov 2026", "29 Oct 2026"),
)


def amount(value: str, language: str) -> str:
    return value if language == "en" else value.replace(".", ",")


def receipt_lines(spec: Receipt) -> list[str]:
    en = spec.language == "en"

    def money(value: str) -> str:
        return amount(value, spec.language)

    subtotal = sum((Decimal(v) for _d, v in spec.items), Decimal("0"))
    if subtotal + Decimal(spec.vat or "0") != Decimal(spec.total):
        raise ValueError(f"{spec.key}: items and VAT do not add up to the total")
    lines = [
        "*** FICTIONAL ***",
        spec.vendor,
        spec.address,
        f"{'Date' if en else 'Fecha'}: {spec.date_text}",
        "",
    ]
    lines += [f"{d[:22]:<22}{money(v):>9}" for d, v in spec.items]
    lines.append("-" * 31)
    if spec.vat is not None:
        lines.append(f"{'Subtotal' if en else 'Base imponible':<22}{money(str(subtotal)):>9}")
        lines.append(f"{spec.vat_rate:<22}{money(spec.vat):>9}")
    total = f"TOTAL {spec.currency}"
    lines.append(f"{total:<22}{money(spec.total):>9}")
    lines += [
        "",
        "Thank you" if en else "Gracias por su visita",
        "Test receipt, not valid" if en else "Recibo de prueba, sin validez",
    ]
    return lines


def whiteboard_lines() -> list[str]:
    lines = ["FICTIONAL", WHITEBOARD.title]
    for number, (item, due) in enumerate(
        zip(WHITEBOARD.items, WHITEBOARD.due_text, strict=True), start=1
    ):
        lines.append(f"{number}. {item.action} - {item.owner} - {due}")
    return lines


def _clean(lines: list[str]) -> Image.Image:
    font = ImageFont.truetype(str(RECEIPT_FONT), size=24)
    page = Image.new("RGB", (560, 80 + 36 * len(lines)), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    for number, line in enumerate(lines):
        draw.text((32, 40 + 36 * number), line, fill=(20, 20, 20), font=font)
    return page


def _coefficients(target: list[tuple[float, float]], source: list[tuple[float, float]]) -> Any:
    rows = []
    for (x, y), (u, v) in zip(target, source, strict=True):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    matrix = np.array(rows, dtype=float)
    vector = np.array(source, dtype=float).reshape(8)
    return np.linalg.solve(matrix, vector).tolist()


def _photo(paper: Image.Image, seed: int, size: tuple[int, int] = (1200, 1600)) -> Image.Image:
    """Lay the paper on a table and photograph it: tilt, perspective, crease, light, blur."""
    rng = random.Random(seed)  # noqa: S311 - reproducible image noise, not security
    width, height = size
    table = Image.merge(
        "RGB",
        [
            Image.linear_gradient("L").resize(size).point(lambda p, base=base: base + p // 6)
            for base in (120, 92, 66)
        ],
    )
    paper = paper.copy()
    draw = ImageDraw.Draw(paper)
    crease = rng.randrange(paper.height // 3, 2 * paper.height // 3)
    draw.line(
        [(0, crease), (paper.width, crease + rng.randrange(-8, 8))], fill=(205, 205, 200), width=3
    )
    draw.line([(0, crease + 3), (paper.width, crease + 4)], fill=(245, 245, 240), width=2)
    scale = min(0.62 * width / paper.width, 0.82 * height / paper.height)
    paper = paper.resize(
        (int(paper.width * scale), int(paper.height * scale)), Image.Resampling.LANCZOS
    )
    w, h = paper.size
    jitter = [(rng.uniform(0, 0.06) * w, rng.uniform(0, 0.04) * h) for _ in range(4)]
    target = [
        (jitter[0][0], jitter[0][1]),
        (w - jitter[1][0], jitter[1][1]),
        (w - jitter[2][0], h - jitter[2][1]),
        (jitter[3][0], h - jitter[3][1]),
    ]
    coeffs = _coefficients(target, [(0, 0), (w, 0), (w, h), (0, h)])
    mask = Image.new("L", paper.size, 255)
    paper = paper.transform(
        paper.size,
        Image.Transform.PERSPECTIVE,
        coeffs,
        Image.Resampling.BICUBIC,
        fillcolor=(0, 0, 0),
    )
    mask = mask.transform(
        mask.size, Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC, fillcolor=0
    )
    angle = rng.uniform(3.0, 7.0) * rng.choice((-1, 1))
    paper = paper.rotate(angle, Image.Resampling.BICUBIC, expand=True)
    mask = mask.rotate(angle, Image.Resampling.BICUBIC, expand=True)
    table.paste(paper, ((width - paper.width) // 2, (height - paper.height) // 2), mask)
    light = Image.radial_gradient("L").resize((width * 2, height * 2))
    light = light.crop(
        (
            rng.randrange(0, width),
            rng.randrange(0, height // 2),
            rng.randrange(0, width) + width,
            rng.randrange(0, height // 2) + height,
        )
    )
    light = light.resize(size).point(lambda p: 255 - p // 3)
    photo = ImageChops.multiply(table, Image.merge("RGB", [light, light, light]))
    return photo.filter(ImageFilter.GaussianBlur(1.1))


def _whiteboard() -> Image.Image:
    font = ImageFont.truetype(str(HAND_FONT), size=58)
    board = Image.new("RGB", (1500, 1000), (236, 238, 236))
    draw = ImageDraw.Draw(board)
    draw.rectangle([(0, 0), (1499, 999)], outline=(120, 120, 125), width=18)
    colours = [(170, 30, 30), (20, 40, 120), (25, 25, 25), (20, 40, 120), (25, 25, 25)]
    for number, line in enumerate(whiteboard_lines()):
        draw.text((90, 80 + 150 * number), line, fill=colours[number], font=font)
    return board


def _jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=82)
    return buffer.getvalue()


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _heic(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    pillow_heif.from_pillow(image).save(buffer, quality=80)
    return buffer.getvalue()


def build(out: Path) -> Built:
    built = Built()

    def save(client: str, name: str, role: str, language: str, data: bytes) -> str:
        folder = out / client / FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        relative = f"{PREFIX}/{client}/{FOLDER}/{name}"
        built.files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(data).hexdigest(),
                "task": TASK,
                "client": client,
                "role": role,
                "language": language,
            }
        )
        return relative

    for number, spec in enumerate(RECEIPTS, start=1):
        clean = _clean(receipt_lines(spec))
        photo = _photo(clean, seed=7000 + number)
        files = [
            save(spec.client, f"{spec.key}.png", "receipt-clean", spec.language, _png(clean)),
            save(
                spec.client, f"{spec.key} photo.jpg", "receipt-photo", spec.language, _jpeg(photo)
            ),
        ]
        if spec.heic:
            files.append(
                save(
                    spec.client,
                    f"{spec.key} photo.heic",
                    "receipt-heic",
                    spec.language,
                    _heic(photo),
                )
            )
        built.facts.append(
            {
                "id": f"{slug(spec.client)}.receipt.{slug(spec.key)}",
                "task": TASK,
                "type": "receipt",
                "client": spec.client,
                "language": spec.language,
                "fields": {
                    "vendor": spec.vendor,
                    "date": spec.day.isoformat(),
                    "total": spec.total,
                    "vat": spec.vat,
                    "currency": spec.currency,
                },
                "files": files,
                "location": {"file": files[0], "image": True},
            }
        )
    board = _photo(_whiteboard(), seed=7100, size=(1600, 1200))
    relative = save(WHITEBOARD.client, f"{WHITEBOARD.key}.jpg", "whiteboard", "en", _jpeg(board))
    for number, item in enumerate(WHITEBOARD.items, start=1):
        built.facts.append(
            {
                "id": f"{slug(WHITEBOARD.client)}.whiteboard.action{number}",
                "task": TASK,
                "type": "action",
                "client": WHITEBOARD.client,
                "language": "en",
                "action": item.action,
                "owner": item.owner,
                "due": item.due.isoformat(),
                "spoken": "en",
                "location": {"file": relative, "image": True},
            }
        )
    return built


def write_records(repo: Path, built: Built) -> None:
    merge_records(repo / "data" / "manifests" / "fictional.json", "files", TASK, built.files)
    merge_records(repo / "data" / "facts.json", "facts", TASK, built.facts)
