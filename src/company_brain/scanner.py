"""Folder scanner, allowlist and scope mapping (T15).

Lists files under the configured root, in the allowed folders only, using
file-system metadata. It never opens a file: content checks happen later on
the verified bytes (T9, T17). Folders other than `Clients/`, `Methodology/`
and `Test data (fictional)/{Clients,Public}/` are never listed.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

SUPPORTED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".html",
        ".htm",
        ".eml",
        ".msg",
        ".vtt",
        ".jpg",
        ".jpeg",
        ".png",
        ".heic",
    }
)
POINTER_EXTENSIONS = frozenset({".gdoc", ".gsheet", ".gslides"})

FICTIONAL = "Test data (fictional)"
CLIENT_FOLDERS = frozenset({"Transcripts", "Proposals", "Contracts", "Reports"})
FICTIONAL_FOLDERS = CLIENT_FOLDERS | {"Receipts"}
OWNER_PHOTOS = "Owner photos"
RESERVED_CLIENT_NAMES = frozenset({"public", "shared"})
AREAS = (("Clients",), (FICTIONAL, "Clients"), (FICTIONAL, "Public"), ("Methodology",))

PUBLIC_SCOPE = "public"
SHARED_SCOPE = "shared"


class SourceUnavailable(Exception):
    """The configured root folder is missing or not a folder."""


@dataclass(frozen=True)
class Location:
    scope: str
    client: str | None
    folder: str | None


@dataclass(frozen=True)
class ScannedFile:
    path: Path
    relative: PurePosixPath
    scope: str
    client: str | None
    folder: str | None
    extension: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class ListedEntry:
    """A file reported by name and size only."""

    relative: PurePosixPath
    size: int


@dataclass
class ScanReport:
    root: Path
    files: list[ScannedFile] = field(default_factory=list)
    unsupported: list[ListedEntry] = field(default_factory=list)
    pointers: list[ListedEntry] = field(default_factory=list)
    unlisted: list[ListedEntry] = field(default_factory=list)
    rejected: list[tuple[PurePosixPath, str]] = field(default_factory=list)
    errors: list[tuple[PurePosixPath, str]] = field(default_factory=list)
    tombstoned: int = 0

    @property
    def complete(self) -> bool:
        """True when every allowed folder was listed without error (T20 deletions)."""
        return not self.errors


def _client(name: str) -> str | None:
    if name.startswith(".") or ":" in name or name in RESERVED_CLIENT_NAMES:
        return None
    return unicodedata.normalize("NFC", name)


def location_of(parts: tuple[str, ...]) -> Location | None:
    """Scope of a file from its path relative to the root, or None if not allowed."""
    if any(part.startswith(".") for part in parts):
        return None
    if len(parts) == 4 and parts[0] == "Clients" and parts[2] in CLIENT_FOLDERS:
        client = _client(parts[1])
        return Location(client, client, parts[2]) if client else None
    if len(parts) >= 5 and parts[:2] == (FICTIONAL, "Clients"):
        client = _client(parts[2])
        folder = parts[3]
        nested_ok = len(parts) == 6 and folder == "Receipts" and parts[4] == OWNER_PHOTOS
        if client and folder in FICTIONAL_FOLDERS and (len(parts) == 5 or nested_ok):
            return Location(f"fictional:{client}", client, folder)
        return None
    if len(parts) >= 3 and parts[:2] == (FICTIONAL, "Public"):
        return Location(PUBLIC_SCOPE, None, None)
    if len(parts) >= 2 and parts[0] == "Methodology":
        return Location(SHARED_SCOPE, None, None)
    return None


def _inside(path: str, directory: str) -> bool:
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def scan(
    root: Path,
    backup_dir: Path | None = None,
    tombstones: Collection[str] = frozenset(),
    is_listed: Callable[[Path], bool] | None = None,
) -> ScanReport:
    """List supported files in the allowed folders, with their scope.

    `is_listed` is the test-mode manifest check (T9): unlisted files are
    reported by name and size and not passed on.
    """
    if not root.is_dir():
        raise SourceUnavailable(f"The documents folder is not available: {root}")
    root_real = os.path.realpath(root)
    backup_real = os.path.realpath(backup_dir) if backup_dir is not None else None
    report = ScanReport(root=Path(root_real))
    seen: set[str] = set()

    def reject(relative: PurePosixPath, reason: str) -> None:
        report.rejected.append((relative, reason))

    def add_file(link_path: str, relative: PurePosixPath) -> None:
        resolved = os.path.realpath(link_path)
        if not _inside(resolved, root_real) or (
            backup_real is not None and _inside(resolved, backup_real)
        ):
            reject(relative, "points outside the allowed folders")
            return
        target = PurePosixPath(os.path.relpath(resolved, root_real))
        location = location_of(target.parts)
        if location is None:
            linked = os.path.islink(link_path)
            reject(
                relative,
                "points outside the allowed folders" if linked else "is not in an expected folder",
            )
            return
        try:
            stat = os.stat(resolved)
        except OSError:
            reject(relative, "could not be read (broken link or no access)")
            return
        if location.scope in tombstones:
            report.tombstoned += 1
            return
        extension = Path(resolved).suffix.lower()
        entry = ListedEntry(relative, stat.st_size)
        if extension in POINTER_EXTENSIONS:
            report.pointers.append(entry)
            return
        if extension not in SUPPORTED_EXTENSIONS:
            report.unsupported.append(entry)
            return
        if resolved in seen:
            return
        seen.add(resolved)
        if is_listed is not None and not is_listed(Path(resolved)):
            report.unlisted.append(entry)
            return
        report.files.append(
            ScannedFile(
                path=Path(resolved),
                relative=relative,
                scope=location.scope,
                client=location.client,
                folder=location.folder,
                extension=extension,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
            )
        )

    def walk(directory: str, relative: PurePosixPath) -> None:
        try:
            with os.scandir(directory) as entries:
                listing = sorted(entries, key=lambda e: e.name)
        except OSError:
            report.errors.append((relative, "could not list folder"))
            return
        for entry in listing:
            child = relative / entry.name
            if entry.name.startswith("."):
                reject(child, "hidden")
            elif entry.is_symlink():
                if os.path.isdir(entry.path):
                    reject(child, "linked folder, not followed")
                else:
                    add_file(entry.path, child)
            elif entry.is_dir(follow_symlinks=False):
                if backup_real is not None and _inside(os.path.realpath(entry.path), backup_real):
                    reject(child, "backup folder, never read")
                else:
                    walk(entry.path, child)
            else:
                add_file(entry.path, child)

    for area in AREAS:
        directory = os.path.join(root_real, *area)
        relative = PurePosixPath(*area)
        if os.path.islink(directory):
            reject(relative, "linked folder, not followed")
        elif os.path.isdir(directory):
            walk(directory, relative)
    return report
