"""T10: the recorded Drive root is the shared Company Brain folder, the folder
tree exists, the backup folder sits next to the root, and every file in the
root is on the Mac rather than a cloud placeholder.

Listed in config/real_resource_allowlist.toml: read-only on the root, and may
create the folder tree. Only names and file flags are read, never contents.
"""

import os
from pathlib import Path

import pytest

from company_brain.drive import (
    BACKUP_FOLDER,
    FOLDER_ID,
    SUBFOLDERS,
    folder_id_of,
    load_sources,
    placeholders,
)
from company_brain.isolation import real_resources

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def sources() -> dict[str, Path]:
    return load_sources(REPO / "config" / "sources.toml")


def test_root_is_the_shared_folder(sources: dict[str, Path]) -> None:
    with real_resources("drive"):
        assert folder_id_of(sources["root"]) == FOLDER_ID


def test_folder_tree_exists_and_backups_are_outside_the_root(sources: dict[str, Path]) -> None:
    root, backups = sources["root"], sources["backups"]
    with real_resources("drive"):
        for folder in SUBFOLDERS:
            assert (root / folder).is_dir(), folder
        assert backups.is_dir()
    assert backups == root.parent / BACKUP_FOLDER
    assert not os.path.realpath(backups).startswith(os.path.realpath(root) + os.sep)


def test_files_in_the_root_are_downloaded(sources: dict[str, Path]) -> None:
    with real_resources("drive"):
        assert placeholders(sources["root"]) == []
