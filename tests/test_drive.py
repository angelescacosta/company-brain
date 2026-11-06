"""T10 unit tests: finding the root by folder ID, the folder tree and placeholders,
on a temporary folder with a fake attribute reader."""

from pathlib import Path

import pytest

from company_brain.availability import SF_DATALESS
from company_brain.drive import (
    BACKUP_FOLDER,
    SUBFOLDERS,
    RootNotFound,
    ensure_tree,
    find_root,
    load_sources,
    placeholders,
    write_sources,
)


def test_find_root_looks_only_at_top_level_folders(tmp_path: Path) -> None:
    my_drive = tmp_path / "My Drive"
    for name in ("Tax Documents", "Company Brain", "Nested"):
        (my_drive / name).mkdir(parents=True)
    (my_drive / "Nested" / "Deep").mkdir()
    ids = {"Company Brain": "ID1", "Tax Documents": "TAX", "Deep": "ID2"}
    asked: list[str] = []

    def read_id(path: Path) -> str | None:
        asked.append(path.name)
        return ids.get(path.name)

    assert find_root(my_drive, "ID1", read_id) == my_drive / "Company Brain"
    with pytest.raises(RootNotFound):
        find_root(my_drive, "ID2", read_id)
    assert "Deep" not in asked


def test_ensure_tree_is_idempotent_and_backups_sit_next_to_root(tmp_path: Path) -> None:
    root = tmp_path / "Company Brain"
    root.mkdir()
    backups = ensure_tree(root)
    assert ensure_tree(root) == backups == tmp_path / BACKUP_FOLDER
    for folder in SUBFOLDERS:
        assert (root / folder).is_dir()


def test_placeholders_reports_dataless_entries(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "local.pdf").write_bytes(b"x")
    (tmp_path / "a" / "cloud.pdf").write_bytes(b"")
    (tmp_path / "cloud dir").mkdir()

    def flags(path: Path) -> int:
        return SF_DATALESS if path.name.startswith("cloud") else 0

    assert placeholders(tmp_path, flags) == ["a/cloud.pdf", "cloud dir"]


def test_sources_round_trip(tmp_path: Path) -> None:
    file = tmp_path / "sources.toml"
    root = tmp_path / "My Drive" / "Company Brain"
    write_sources(file, root, "ID1")
    assert load_sources(file) == {"root": root, "backups": root.parent / BACKUP_FOLDER}
