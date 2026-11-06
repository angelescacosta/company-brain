"""T9 PBT: unlisted paths are never read, altered files are never parsed, and
conversion receives exactly the verified bytes.

Protects against indexing the owner's real documents by accident. File reads
and parser calls are recorded on a fake file system.
"""

import hashlib
from pathlib import PurePosixPath

import pytest
from hypothesis import given
from hypothesis import strategies as st

from company_brain.content_guard import (
    DigestMismatch,
    GuardedReader,
    Manifest,
    Unlisted,
    guarded_convert,
)

ROOT = PurePosixPath("/root")
names = st.text(alphabet="abcdefghij0123", min_size=1, max_size=8)


@st.composite
def file_sets(draw: st.DrawFn) -> dict[str, tuple[str, bytes]]:
    """Map of relative path to (kind, bytes); kind is listed, unlisted or altered."""
    paths = draw(st.lists(names, min_size=1, max_size=12, unique=True))
    return {
        f"Methodology/{p}.pdf": (
            draw(st.sampled_from(["listed", "unlisted", "altered"])),
            draw(st.binary(min_size=0, max_size=64)),
        )
        for p in paths
    }


class FakeFiles:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.reads: list[str] = []

    def read(self, path: str) -> bytes:
        self.reads.append(path)
        return self.files[str(PurePosixPath(path).relative_to(ROOT))]

    def size(self, path: str) -> int:
        return len(self.files[str(PurePosixPath(path).relative_to(ROOT))])


@given(file_sets())
def test_guard_properties(spec: dict[str, tuple[str, bytes]]) -> None:
    on_disk = {}
    digests = {}
    for path, (kind, data) in spec.items():
        if kind == "altered":
            on_disk[path] = data + b"\x00changed"
            digests[path] = hashlib.sha256(data).hexdigest()
        else:
            on_disk[path] = data
            if kind == "listed":
                digests[path] = hashlib.sha256(data).hexdigest()
    files = FakeFiles(on_disk)
    reader = GuardedReader(
        root=ROOT,
        manifest=Manifest(digests),
        read_bytes=files.read,
        file_size=files.size,
        resolve=lambda p: p,
    )
    parsed: list[tuple[str, bytes]] = []

    def parser(name: str, data: bytes) -> str:
        parsed.append((name, data))
        return "converted"

    for path, (kind, _) in spec.items():
        absolute = str(ROOT / path)
        if kind == "unlisted":
            with pytest.raises(Unlisted) as info:
                guarded_convert(reader, absolute, parser)
            assert info.value.size == len(on_disk[path])
            assert info.value.name == PurePosixPath(path).name
        elif kind == "altered":
            with pytest.raises(DigestMismatch):
                guarded_convert(reader, absolute, parser)
        else:
            assert guarded_convert(reader, absolute, parser) == "converted"

    unlisted = {str(ROOT / p) for p, (k, _) in spec.items() if k == "unlisted"}
    assert unlisted.isdisjoint(files.reads)
    altered = {PurePosixPath(p).name for p, (k, _) in spec.items() if k == "altered"}
    assert altered.isdisjoint(name for name, _ in parsed)
    listed = {p: on_disk[p] for p, (k, _) in spec.items() if k == "listed"}
    assert sorted(parsed) == sorted((PurePosixPath(p).name, d) for p, d in listed.items())
    readable = [p for p, (k, _) in spec.items() if k != "unlisted"]
    assert sorted(files.reads) == sorted(str(ROOT / p) for p in readable)
