"""The Drive root and its folder tree (T10).

The root is the folder the owner shared, found by its Drive folder ID, which
Drive for Desktop stores as an extended attribute. Only the top-level folders
of the mount are asked for their ID, so no other folder is ever opened.
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from collections.abc import Callable
from pathlib import Path

from company_brain.availability import SF_DATALESS

FOLDER_ID = "YOUR_DRIVE_FOLDER_ID"
FOLDER_ID_ATTRIBUTE = "com.google.drivefs.item-id#S"
SUBFOLDERS = ("Clients", "Methodology", "Test data (fictional)/Clients")
BACKUP_FOLDER = "Company Brain Backups"


class RootNotFound(RuntimeError):
    """No top-level Drive folder has the expected folder ID."""


def folder_id_of(path: Path) -> str | None:
    # Python on macOS has no os.getxattr; the system xattr tool reads it.
    result = subprocess.run(  # noqa: S603 - fixed program, path passed as one argument
        ["/usr/bin/xattr", "-p", FOLDER_ID_ATTRIBUTE, str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def find_root(
    my_drive: Path, folder_id: str, read_id: Callable[[Path], str | None] = folder_id_of
) -> Path:
    for entry in sorted(my_drive.iterdir()):
        if entry.is_dir() and read_id(entry) == folder_id:
            return entry
    raise RootNotFound(f"no folder with ID {folder_id} at the top of {my_drive}")


def ensure_tree(root: Path) -> Path:
    """Create the subfolders and the backup folder next to the root; return the latter."""
    for folder in SUBFOLDERS:
        (root / folder).mkdir(parents=True, exist_ok=True)
    backups = root.parent / BACKUP_FOLDER
    backups.mkdir(exist_ok=True)
    return backups


def _flags(path: Path) -> int:
    return os.lstat(path).st_flags


def placeholders(root: Path, flags: Callable[[Path], int] = _flags) -> list[str]:
    """Entries under the root that are still only in the cloud (by name, never opened)."""
    found = []
    for directory, dirnames, filenames in os.walk(root):
        base = Path(directory)
        for name in sorted(dirnames):
            if flags(base / name) & SF_DATALESS:
                found.append((base / name).relative_to(root).as_posix())
                dirnames.remove(name)
        for name in sorted(filenames):
            if flags(base / name) & SF_DATALESS:
                found.append((base / name).relative_to(root).as_posix())
    return sorted(found)


def write_sources(file: Path, root: Path, folder_id: str) -> None:
    file.write_text(
        "# Written by scripts/setup_drive_tree.py (T10). The Drive root is a real\n"
        "# resource: tests may use it only through the allowlist.\n"
        "[root]\n"
        f"path = {str(root)!r}\n"
        f"folder_id = {folder_id!r}\n",
        encoding="utf-8",
    )


def load_sources(file: Path) -> dict[str, Path]:
    root = Path(tomllib.loads(file.read_text(encoding="utf-8"))["root"]["path"])
    return {"root": root, "backups": root.parent / BACKUP_FOLDER}
