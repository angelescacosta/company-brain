"""T12: public test documents, pinned by URL and checksum.

The Drive copies are checked through the content guard (read-only, listed in
config/real_resource_allowlist.toml); everything else is checked against the
committed records.
"""

import json
import re
from pathlib import Path

import pytest

from company_brain import public_data
from company_brain.content_guard import GuardedReader, Manifest
from company_brain.drive import load_sources
from company_brain.isolation import real_resources

REPO = Path(__file__).resolve().parents[1]
CORD = {0: "60.000", 1: "91000", 2: "28,000", 4: "174,600", 7: "111,000"}


def test_sources_are_pinned() -> None:
    contracts = [d for d in public_data.DOWNLOADS if d.role == "contract"]
    assert len(contracts) == 10
    assert len({d.category for d in contracts}) == 10
    assert all(public_data.CUAD_REVISION in d.url for d in contracts)
    reports = [d for d in public_data.DOWNLOADS if d.role == "annual-report"]
    assert len(reports) == 2
    assert {d.language for d in reports} == {"en", "es"}
    assert {d.licence for d in reports} == {"OGL-UK-3.0", "CC-BY-4.0"}
    assert public_data.CORD_REVISION in public_data.CORD_PARQUET.url
    for item in (*public_data.DOWNLOADS, public_data.CORD_PARQUET, *public_data.CORD_IMAGES):
        assert re.fullmatch(r"[0-9a-f]{64}", item.sha256)
    for d in public_data.DOWNLOADS:
        assert d.url.startswith("https://") and d.path.isascii()


def test_exactly_the_pinned_cord_images_with_their_amounts() -> None:
    """T12.AC3 (records): the 5 pinned CORD images and their published total-price labels."""
    assert {i.image_id: i.label for i in public_data.CORD_IMAGES} == CORD
    facts = [f for f in public_data.facts() if f["type"] == "receipt"]
    assert [f["image_id"] for f in facts] == sorted(CORD)
    for fact in facts:
        label = CORD[fact["image_id"]]
        assert fact["label"] == label
        assert fact["fields"] == {"total": re.sub(r"\D", "", label)}
        assert fact["files"] == [public_data.cord_path(fact["image_id"])]


@pytest.mark.parametrize(
    ("label", "amount"),
    [("60.000", "60000"), ("28,000", "28000"), ("91000", "91000"), ("Rp. 111,000", "111000")],
)
def test_cord_amounts_normalise(label: str, amount: str) -> None:
    assert public_data.amount(label) == amount


def test_records_match_the_sources() -> None:
    manifest = json.loads((REPO / "data" / "manifests" / "public.json").read_text())
    assert manifest["files"] == public_data.entries()
    assert len(manifest["files"]) == 17
    facts = json.loads((REPO / "data" / "facts.json").read_text())
    assert [f for f in facts["facts"] if f["task"] == public_data.TASK] == public_data.facts()


def test_downloaded_files_match_their_checksums() -> None:
    """T12.AC1 and AC3: every Drive copy passes the content guard; nothing else is there."""
    root = load_sources(REPO / "config" / "sources.toml")["root"]
    reader = GuardedReader(root, Manifest.load(REPO / "data" / "manifests"))
    with real_resources("drive"):
        for entry in public_data.entries():
            assert reader.read(root / entry["path"]).sha256 == entry["sha256"]
        present = sorted(
            p.relative_to(root).as_posix()
            for p in (root / public_data.PREFIX).rglob("*")
            if p.is_file() and not p.name.startswith(".")
        )
    assert present == sorted(e["path"] for e in public_data.entries())
