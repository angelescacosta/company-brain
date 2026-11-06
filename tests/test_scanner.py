"""T15 unit tests: missing root, unsupported types, layouts, tombstones and listing."""

from pathlib import Path

import pytest

from company_brain.scanner import SUPPORTED_EXTENSIONS, SourceUnavailable, scan


def touch(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_missing_root_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceUnavailable):
        scan(tmp_path / "not-there")


def test_root_that_is_a_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceUnavailable):
        scan(touch(tmp_path / "file"))


def test_unsupported_extensions_listed_not_passed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    touch(root / "Clients" / "acme" / "Reports" / "notes.txt")
    touch(root / "Clients" / "acme" / "Reports" / "old.doc")
    touch(root / "Clients" / "acme" / "Reports" / "report.PDF")
    report = scan(root)
    assert [f.path.name for f in report.files] == ["report.PDF"]
    assert sorted(u.relative.name for u in report.unsupported) == ["notes.txt", "old.doc"]


def test_supported_extensions() -> None:
    assert {
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
    } == SUPPORTED_EXTENSIONS


def test_google_pointers_are_reported_separately(tmp_path: Path) -> None:
    root = tmp_path / "root"
    touch(root / "Clients" / "acme" / "Proposals" / "draft.gdoc", '{"doc_id": "x"}')
    report = scan(root)
    assert report.files == []
    assert [p.relative.name for p in report.pointers] == ["draft.gdoc"]


def test_scope_mapping(tmp_path: Path) -> None:
    root = tmp_path / "root"
    touch(root / "Clients" / "acme" / "Contracts" / "c.docx")
    touch(root / "Test data (fictional)" / "Clients" / "lumen" / "Receipts" / "r.png")
    touch(
        root
        / "Test data (fictional)"
        / "Clients"
        / "lumen"
        / "Receipts"
        / "Owner photos"
        / "p.heic"
    )
    touch(root / "Test data (fictional)" / "Public" / "CUAD" / "k.pdf")
    touch(root / "Methodology" / "m.docx")
    scopes = {f.path.name: (f.scope, f.client) for f in scan(root).files}
    assert scopes == {
        "c.docx": ("acme", "acme"),
        "r.png": ("fictional:lumen", "lumen"),
        "p.heic": ("fictional:lumen", "lumen"),
        "k.pdf": ("public", None),
        "m.docx": ("shared", None),
    }


def test_invalid_layouts_reported_not_guessed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    touch(root / "Clients" / "acme" / "loose.pdf")
    touch(root / "Clients" / "acme" / "Invoices" / "i.pdf")
    touch(root / "Clients" / "acme" / "Reports" / "2025" / "deep.pdf")
    touch(root / "Clients" / "loose.pdf")
    touch(root / "Clients" / "public" / "Reports" / "r.pdf")
    touch(root / "Clients" / "a:b" / "Reports" / "r.pdf")
    touch(root / "Clients" / "acme" / "Receipts" / "r.png")
    report = scan(root)
    assert report.files == []
    assert len(report.rejected) == 7
    assert all(reason for _, reason in report.rejected)


def test_hidden_files_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    touch(root / "Clients" / "acme" / "Reports" / ".DS_Store")
    touch(root / "Clients" / ".hidden" / "Reports" / "r.pdf")
    report = scan(root)
    assert report.files == []
    assert len(report.rejected) == 2


def test_other_top_level_folders_never_listed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    touch(root / "Tax" / "2025" / "return.pdf")
    touch(root / "Bank" / "statement.pdf")
    touch(root / "loose.pdf")
    report = scan(root)
    assert report.files == [] and report.unsupported == [] and report.pointers == []
    assert all("Tax" not in str(r) and "Bank" not in str(r) for r, _ in report.rejected)


def test_backup_folder_inside_root_is_excluded(tmp_path: Path) -> None:
    root = tmp_path / "root"
    touch(root / "Methodology" / "Backups" / "archive.pdf")
    touch(root / "Methodology" / "method.pdf")
    report = scan(root, backup_dir=root / "Methodology" / "Backups")
    assert [f.path.name for f in report.files] == ["method.pdf"]


def test_linked_folders_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    touch(tmp_path / "elsewhere" / "x.pdf")
    (root / "Clients" / "acme").mkdir(parents=True)
    (root / "Clients" / "acme" / "Reports").symlink_to(tmp_path / "elsewhere")
    report = scan(root)
    assert report.files == []
    assert len(report.rejected) == 1


def test_tombstoned_clients_excluded(tmp_path: Path) -> None:
    root = tmp_path / "root"
    touch(root / "Clients" / "gone" / "Reports" / "r.pdf")
    touch(root / "Test data (fictional)" / "Clients" / "gone" / "Reports" / "r.pdf")
    touch(root / "Clients" / "kept" / "Reports" / "r.pdf")
    report = scan(root, tombstones={"gone", "fictional:gone"})
    assert [f.scope for f in report.files] == ["kept"]


def test_unlisted_files_reported_by_name_and_size(tmp_path: Path) -> None:
    root = tmp_path / "root"
    listed = touch(root / "Methodology" / "listed.pdf")
    touch(root / "Methodology" / "unlisted.pdf", "12345")
    report = scan(root, is_listed=lambda path: path == listed.resolve())
    assert [f.path.name for f in report.files] == ["listed.pdf"]
    assert [(u.relative.name, u.size) for u in report.unlisted] == [("unlisted.pdf", 5)]


def test_scan_errors_are_recorded(tmp_path: Path) -> None:
    root = tmp_path / "root"
    locked = root / "Clients" / "acme" / "Reports"
    touch(locked / "r.pdf")
    locked.chmod(0o000)
    try:
        report = scan(root)
    finally:
        locked.chmod(0o755)
    assert report.errors
    assert not report.complete
