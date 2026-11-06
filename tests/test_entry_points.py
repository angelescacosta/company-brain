"""T9.AC4 (staged): every content entry point goes through the guarded reader.

Each task that adds an entry point (ingestion T20, Summarise T26, restore T40)
registers it in `content_guard.ENTRY_POINTS` and adds a case below; T45 checks
the list is complete. Scanning and ingestion never write a manifest.
"""

from pathlib import Path

import pytest

from company_brain import content_guard, scanner
from company_brain.content_guard import ENTRY_POINTS, GuardedReader, Manifest, VerifiedBytes


class SpyReader(GuardedReader):
    def __init__(self) -> None:
        super().__init__(Path("/root"), Manifest({}))
        self.calls: list[str] = []

    def read(self, path: str | Path) -> VerifiedBytes:
        self.calls.append(str(path))
        return VerifiedBytes(relative="x.pdf", data=b"%PDF", sha256="0" * 64)


def test_every_registered_entry_point_uses_the_reader() -> None:
    assert ENTRY_POINTS, "no entry point registered"
    for name, entry in ENTRY_POINTS.items():
        spy = SpyReader()
        entry(spy, "/root/x.pdf", lambda _name, _data: "ok")
        assert spy.calls == ["/root/x.pdf"], name


def test_scanning_never_writes_a_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[object] = []
    monkeypatch.setattr(content_guard, "_write_json", lambda *a: writes.append(a))
    root = tmp_path / "root"
    (root / "Methodology").mkdir(parents=True)
    (root / "Methodology" / "m.pdf").write_bytes(b"x")
    reader = GuardedReader(root, Manifest({}))
    report = scanner.scan(root, is_listed=reader.is_listed)
    assert report.unlisted and not report.files
    assert writes == []
