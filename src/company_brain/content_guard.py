"""Test-mode content guard (T9).

While `config/profile.toml` has `mode = "test"` (the only mode this project
allows), content enters the system only through `GuardedReader`. A path not
listed in the fictional or public manifest is refused before any byte is read
and reported by name and size only. A listed path is read once into memory,
its digest is checked, and only those same bytes go on to conversion.
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

GO_LIVE_MESSAGE = (
    "public and fictional demonstration only; confidential data needs a separate go-live review"
)
MANIFEST_NAMES = ("fictional.json", "public.json")
# Only these authoring steps may write a manifest; scanning and ingestion never do.
MANIFEST_WRITERS = frozenset({"T11", "T11R", "T11P", "T12", "T13", "H4-refresh"})


class Unlisted(Exception):
    """The file is not in the test lists; nothing was read."""

    def __init__(self, name: str, size: int | None) -> None:
        self.name = name
        self.size = size
        shown = f"{size} bytes" if size is not None else "size unknown"
        super().__init__(f"{name} ({shown}) is not in the test lists and was not read")


class DigestMismatch(Exception):
    """The file's bytes differ from the manifest; they were discarded unparsed."""


class OutsideRoot(Exception):
    """The path is not inside the documents folder."""


class ModeRefused(Exception):
    """Only test mode is allowed in this project."""


@dataclass(frozen=True)
class Profile:
    mode: str
    demo_profile: str


def _refuse(mode: str) -> ModeRefused:
    return ModeRefused(f"Mode {mode!r} is not allowed: {GO_LIVE_MESSAGE}.")


def load_profile(path: Path) -> Profile:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    mode = data.get("mode", "")
    if mode != "test":
        raise _refuse(mode)
    return Profile(mode=mode, demo_profile=data["demo_profile"])


def set_mode(path: Path, mode: str) -> None:
    """Change the mode; every mode other than "test" is refused in this project."""
    if mode != "test":
        raise _refuse(mode)
    load_profile(path)


@dataclass(frozen=True)
class Manifest:
    digests: Mapping[str, str]

    @classmethod
    def load(cls, folder: Path) -> Manifest:
        digests: dict[str, str] = {}
        for name in MANIFEST_NAMES:
            path = folder / name
            if path.exists():
                for entry in json.loads(path.read_text(encoding="utf-8"))["files"]:
                    digests[entry["path"]] = entry["sha256"]
        return cls(digests)


@dataclass(frozen=True)
class VerifiedBytes:
    relative: str
    data: bytes
    sha256: str


def _read_once(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _size(path: str) -> int:
    return os.stat(path).st_size


class GuardedReader:
    """The one way content is read while the system is in test mode."""

    def __init__(
        self,
        root: Path | PurePosixPath,
        manifest: Manifest,
        read_bytes: Callable[[str], bytes] = _read_once,
        file_size: Callable[[str], int] = _size,
        resolve: Callable[[str], str] = os.path.realpath,
    ) -> None:
        self._root = resolve(str(root))
        self._manifest = manifest
        self._read_bytes = read_bytes
        self._file_size = file_size
        self._resolve = resolve

    def _relative(self, resolved: str) -> str:
        root = self._root.rstrip("/")
        if not resolved.startswith(root + "/"):
            raise OutsideRoot(f"{PurePosixPath(resolved).name} is outside the documents folder")
        return resolved[len(root) + 1 :]

    def is_listed(self, path: str | Path) -> bool:
        try:
            return self._relative(self._resolve(str(path))) in self._manifest.digests
        except OutsideRoot:
            return False

    def read(self, path: str | Path) -> VerifiedBytes:
        resolved = self._resolve(str(path))
        relative = self._relative(resolved)
        expected = self._manifest.digests.get(relative)
        if expected is None:
            try:
                size: int | None = self._file_size(resolved)
            except OSError:
                size = None
            raise Unlisted(PurePosixPath(str(path)).name, size)
        data = self._read_bytes(resolved)
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected:
            del data
            raise DigestMismatch(
                f"{PurePosixPath(relative).name} changed since it was listed; not converted"
            )
        return VerifiedBytes(relative=relative, data=data, sha256=digest)


def guarded_convert[T](
    reader: GuardedReader, path: str | Path, convert: Callable[[str, bytes], T]
) -> T:
    """Convert a file from its verified bytes only (no second read)."""
    verified = reader.read(path)
    return convert(PurePosixPath(verified.relative).name, verified.data)


# Every function that brings content into the system; checked by tests/test_entry_points.py.
ENTRY_POINTS: dict[str, Callable[[GuardedReader, str, Callable[[str, bytes], Any]], Any]] = {
    "convert": guarded_convert,
}


def _write_json(path: Path, data: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def write_manifest_entries(path: Path, entries: Iterable[Mapping[str, Any]], writer: str) -> None:
    """Add or replace manifest entries; only the named authoring scripts may call this."""
    if writer not in MANIFEST_WRITERS:
        raise PermissionError(f"{writer} may not write a manifest")
    current: dict[str, Any] = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {"version": 1, "files": []}
    )
    files = {entry["path"]: entry for entry in current["files"]}
    for entry in entries:
        files[entry["path"]] = {**entry, "writer": writer}
    current["files"] = [files[key] for key in sorted(files)]
    _write_json(path, current)
