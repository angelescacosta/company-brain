"""Download the public test documents into the Drive root and record them (T12).

Usage: uv run python scripts/fetch_public_data.py

Downloads with curl into a temporary folder, stops on any checksum mismatch,
then copies the files into `Test data (fictional)/Public/` and writes the T12
entries of `data/manifests/public.json` and `data/facts.json`. Safe to run
again; refuses to overwrite a file T12 has not recorded, without opening it.
"""

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pyarrow.parquet as pq

from company_brain import public_data
from company_brain.corpus import merge_records
from company_brain.drive import load_sources

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "data" / "manifests" / "public.json"


def fetch(url: str, sha256: str, out: Path) -> bytes:
    subprocess.run(  # noqa: S603 - fixed program, pinned URLs from public_data
        [
            "/usr/bin/curl",
            "--fail",
            "--silent",
            "--location",
            "--max-time",
            "600",
            "-o",
            str(out),
            url,
        ],
        check=True,
    )
    data = out.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise SystemExit(f"Checksum mismatch for {url}: got {digest}")
    return data


def cord_images(parquet: Path) -> dict[int, bytes]:
    wanted = {image.image_id for image in public_data.CORD_IMAGES}
    table = pq.read_table(parquet, columns=["image", "ground_truth"])
    found = {}
    for image, truth in zip(
        table["image"].to_pylist(), table["ground_truth"].to_pylist(), strict=True
    ):
        image_id = json.loads(truth)["meta"]["image_id"]
        if image_id in wanted:
            found[image_id] = image["bytes"]
    return found


def main() -> int:
    root = load_sources(REPO / "config" / "sources.toml")["root"]
    parent = (root / public_data.PREFIX).parent
    if not parent.is_dir():
        print(f"Missing folder {parent}; run scripts/setup_drive_tree.py first.")
        return 1
    recorded = set()
    if MANIFEST.exists():
        recorded = {
            e["path"]
            for e in json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
            if e.get("task") == public_data.TASK
        }
    files: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory() as scratch:
        for index, item in enumerate(public_data.DOWNLOADS):
            data = fetch(item.url, item.sha256, Path(scratch) / f"{index}.download")
            files[f"{public_data.PREFIX}/{item.path}"] = data
        parquet = Path(scratch) / "cord.parquet"
        fetch(public_data.CORD_PARQUET.url, public_data.CORD_PARQUET.sha256, parquet)
        images = cord_images(parquet)
    for image in public_data.CORD_IMAGES:
        data = images[image.image_id]
        if hashlib.sha256(data).hexdigest() != image.sha256:
            raise SystemExit(f"Checksum mismatch for CORD image {image.image_id}")
        files[public_data.cord_path(image.image_id)] = data
    for relative in files:
        if (root / relative).exists() and relative not in recorded:
            print(f"Refusing to overwrite {relative}: it is not a recorded T12 file.")
            return 1
    for relative, data in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(data)
    merge_records(MANIFEST, "files", public_data.TASK, public_data.entries())
    merge_records(REPO / "data" / "facts.json", "facts", public_data.TASK, public_data.facts())
    print(f"{len(files)} public files written to {root / public_data.PREFIX}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
