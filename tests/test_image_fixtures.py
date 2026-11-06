"""T11R: receipt and whiteboard image fixtures.

Ground truth is checked against the authoring records the renderer was given,
never by OCR (reading accuracy is T17's job).
"""

import hashlib
import io
import json
import tomllib
from pathlib import Path

import pillow_heif
import pytest
from fontTools.ttLib import TTFont
from PIL import Image

from company_brain import corpus, image_fixtures
from company_brain.image_fixtures import RECEIPTS, WHITEBOARD

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, corpus.Built]:
    out = tmp_path_factory.mktemp("images")
    return out, image_fixtures.build(out)


def on_disk(out: Path, relative: str) -> Path:
    return out / relative[len(corpus.PREFIX) + 1 :]


def test_matrix_is_complete_and_listed(built: tuple[Path, corpus.Built]) -> None:
    """T11R.AC1: the 19 files exist, each listed in the committed manifest with its hash."""
    out, result = built
    roles = [entry["role"] for entry in result.files]
    assert roles.count("receipt-clean") == 8
    assert roles.count("receipt-photo") == 8
    assert roles.count("receipt-heic") == 2
    assert roles.count("whiteboard") == 1
    assert len(result.files) == 19
    assert sorted(r.language for r in RECEIPTS) == ["en"] * 4 + ["es"] * 4
    suffix = {
        "receipt-clean": ".png",
        "receipt-photo": ".jpg",
        "receipt-heic": ".heic",
        "whiteboard": ".jpg",
    }
    for entry in result.files:
        path = on_disk(out, entry["path"])
        assert path.suffix == suffix[entry["role"]]
        assert "/Receipts/" in entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    manifest = json.loads((REPO / "data" / "manifests" / "fictional.json").read_text())
    assert [e for e in manifest["files"] if e["task"] == image_fixtures.TASK] == result.files
    facts = json.loads((REPO / "data" / "facts.json").read_text())
    assert [f for f in facts["facts"] if f["task"] == image_fixtures.TASK] == result.facts


def test_ground_truth_equals_renderer_inputs(built: tuple[Path, corpus.Built]) -> None:
    """T11R.AC2: receipt and action records carry exactly the values that were drawn."""
    _out, result = built
    receipts = [f for f in result.facts if f["type"] == "receipt"]
    assert len(receipts) == len(RECEIPTS) == 8
    for fact, spec in zip(receipts, RECEIPTS, strict=True):
        assert fact["fields"] == {
            "vendor": spec.vendor,
            "date": spec.day.isoformat(),
            "total": spec.total,
            "vat": spec.vat,
            "currency": spec.currency,
        }
        lines = image_fixtures.receipt_lines(spec)
        assert any(spec.vendor in line for line in lines)
        assert any(spec.date_text in line for line in lines)
        assert any(image_fixtures.amount(spec.total, spec.language) in line for line in lines)
        if spec.vat is not None:
            assert any(image_fixtures.amount(spec.vat, spec.language) in line for line in lines)
        assert lines[0].count("FICTIONAL") == 1
        variants = {Path(p).suffix for p in fact["files"]}
        assert variants == ({".png", ".jpg", ".heic"} if spec.heic else {".png", ".jpg"})
    assert sum(1 for r in RECEIPTS if r.vat is None) >= 1
    actions = [f for f in result.facts if f["type"] == "action"]
    assert [(a["action"], a["owner"], a["due"]) for a in actions] == [
        (item.action, item.owner, item.due.isoformat()) for item in WHITEBOARD.items
    ]
    board = image_fixtures.whiteboard_lines()
    assert "FICTIONAL" in board[0]
    for item in WHITEBOARD.items:
        assert any(item.action in line and item.owner in line for line in board)


def test_rendering_is_reproducible(built: tuple[Path, corpus.Built], tmp_path: Path) -> None:
    """T11R.AC3: rendering twice with the pinned inputs gives identical files."""
    _out, result = built
    assert image_fixtures.build(tmp_path) == result


def test_images_decode_and_photos_differ_from_clean(built: tuple[Path, corpus.Built]) -> None:
    out, result = built
    pillow_heif.register_heif_opener()
    for fact in (f for f in result.facts if f["type"] == "receipt"):
        clean, photo, *heic = (on_disk(out, p) for p in fact["files"])
        with Image.open(clean) as a, Image.open(photo) as b:
            assert a.format == "PNG" and b.format == "JPEG"
            assert a.size != b.size
        for path in heic:
            with Image.open(io.BytesIO(path.read_bytes())) as h, Image.open(photo) as b:
                assert h.size == b.size


def test_fonts_are_pinned_and_cover_every_letter() -> None:
    fonts = {
        f["file"]: f for f in tomllib.loads((REPO / "config" / "fonts.toml").read_text())["font"]
    }
    texts = {
        image_fixtures.RECEIPT_FONT: [
            line for r in RECEIPTS for line in image_fixtures.receipt_lines(r)
        ],
        image_fixtures.HAND_FONT: image_fixtures.whiteboard_lines(),
    }
    for font, lines in texts.items():
        entry = fonts[font.relative_to(REPO).as_posix()]
        assert hashlib.sha256(font.read_bytes()).hexdigest() == entry["sha256"]
        assert "T11R" in entry["used_by"]
        covered = TTFont(str(font)).getBestCmap()
        missing = {ch for line in lines for ch in line if ord(ch) not in covered}
        assert not missing, (font.name, missing)
