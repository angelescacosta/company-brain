"""T9 unit tests: real files, symlinks, manifests, mode switch and manifest writers."""

import hashlib
import json
from pathlib import Path

import pytest

from company_brain.content_guard import (
    GO_LIVE_MESSAGE,
    DigestMismatch,
    GuardedReader,
    Manifest,
    ModeRefused,
    OutsideRoot,
    Unlisted,
    load_profile,
    set_mode,
    write_manifest_entries,
)


def make(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_reads_listed_file_once_and_returns_verified_bytes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    path = make(root, "Methodology/m.pdf", b"%PDF fictional")
    manifest = Manifest({"Methodology/m.pdf": hashlib.sha256(b"%PDF fictional").hexdigest()})
    verified = GuardedReader(root, manifest).read(path)
    assert verified.data == b"%PDF fictional"
    assert verified.relative == "Methodology/m.pdf"


def test_unlisted_reported_by_name_and_size(tmp_path: Path) -> None:
    root = tmp_path / "root"
    path = make(root, "Clients/acme/Proposals/real.pdf", b"12345678")
    with pytest.raises(Unlisted) as info:
        GuardedReader(root, Manifest({})).read(path)
    assert (info.value.name, info.value.size) == ("real.pdf", 8)
    assert "not in the test lists" in str(info.value)


def test_altered_file_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    path = make(root, "Methodology/m.pdf", b"changed")
    manifest = Manifest({"Methodology/m.pdf": hashlib.sha256(b"original").hexdigest()})
    with pytest.raises(DigestMismatch):
        GuardedReader(root, manifest).read(path)


def test_symlink_to_unlisted_file_is_unlisted(tmp_path: Path) -> None:
    root = tmp_path / "root"
    real = make(root, "Clients/acme/Proposals/real.pdf", b"secret")
    link = root / "Methodology" / "m.pdf"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    manifest = Manifest({"Methodology/m.pdf": hashlib.sha256(b"secret").hexdigest()})
    with pytest.raises(Unlisted):
        GuardedReader(root, manifest).read(link)


def test_path_outside_root_refused(tmp_path: Path) -> None:
    outside = make(tmp_path, "elsewhere.pdf", b"x")
    with pytest.raises(OutsideRoot):
        GuardedReader(tmp_path / "root", Manifest({})).read(outside)


def test_manifest_loads_both_lists(tmp_path: Path) -> None:
    folder = tmp_path / "manifests"
    folder.mkdir()
    for name, rel in (("fictional", "a.pdf"), ("public", "b.pdf")):
        (folder / f"{name}.json").write_text(
            json.dumps({"version": 1, "files": [{"path": rel, "sha256": "0" * 64, "size": 1}]})
        )
    assert Manifest.load(folder).digests == {"a.pdf": "0" * 64, "b.pdf": "0" * 64}


def test_missing_manifests_mean_nothing_is_listed(tmp_path: Path) -> None:
    assert Manifest.load(tmp_path / "none").digests == {}


def test_profile_mode_must_be_test(tmp_path: Path) -> None:
    profile = tmp_path / "profile.toml"
    profile.write_text('mode = "test"\ndemo_profile = "full"\n')
    assert load_profile(profile).mode == "test"
    profile.write_text('mode = "live"\ndemo_profile = "full"\n')
    with pytest.raises(ModeRefused) as info:
        load_profile(profile)
    assert GO_LIVE_MESSAGE in str(info.value)


@pytest.mark.parametrize("mode", ["live", "production", "", "TEST"])
def test_switching_mode_refused(tmp_path: Path, mode: str) -> None:
    profile = tmp_path / "profile.toml"
    profile.write_text('mode = "test"\ndemo_profile = "full"\n')
    with pytest.raises(ModeRefused) as info:
        set_mode(profile, mode)
    assert GO_LIVE_MESSAGE in str(info.value)
    assert load_profile(profile).mode == "test"


def test_repository_profile_is_test_mode() -> None:
    profile = load_profile(Path(__file__).resolve().parents[1] / "config" / "profile.toml")
    assert profile.mode == "test"
    assert profile.demo_profile in {"full", "reduced"}


def test_manifest_writer_requires_named_authoring_script(tmp_path: Path) -> None:
    target = tmp_path / "fictional.json"
    with pytest.raises(PermissionError):
        write_manifest_entries(target, [], writer="ingest")
    write_manifest_entries(target, [{"path": "a.pdf", "sha256": "0" * 64, "size": 1}], writer="T11")
    assert json.loads(target.read_text())["files"][0]["path"] == "a.pdf"
