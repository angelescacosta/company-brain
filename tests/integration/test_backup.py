"""T39: nightly backup on the test copies (test database, test data folder, a test key).

Marker `destructive`: it stops and starts the test copies' services.
"""

import json
import subprocess
import tarfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from company_brain import backup, coordinator, signing
from company_brain.settings import Settings

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
AGE_DIR = Path.home() / ".local" / "opt" / "age-1.3.2"


@pytest.fixture
def keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    out = subprocess.run([str(AGE_DIR / "age-keygen")], capture_output=True, text=True, check=True)
    identity = next(line for line in out.stdout.splitlines() if line.startswith("AGE-SECRET-KEY"))
    recipient = subprocess.run(
        [str(AGE_DIR / "age-keygen"), "-y"],
        input=identity + "\n",
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    # The signing key Set up backups keeps in the Keychain (security scan F1).
    monkeypatch.setattr(signing, "stored_key", lambda: signing.key_from_identity(identity))
    return identity, recipient


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    yield Settings.from_env()


def decrypt(path: Path, identity: str, out: Path) -> Path:
    data = subprocess.run(
        [str(AGE_DIR / "age"), "-d", "-i", "/dev/stdin", str(path)],
        input=(identity + "\n").encode(),
        capture_output=True,
        check=True,
    ).stdout
    out.write_bytes(data)
    return out


def test_archive_is_encrypted_complete_and_listed(settings: Settings, keys, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """T39.AC1 and AC3."""
    identity, recipient = keys
    folder = tmp_path / "Company Brain Backups"
    folder.mkdir()
    result = backup.run(settings, recipient, folder, services=backup.NoServices())
    assert result.state == "completed", result
    archives = backup.archives(folder)
    assert [a.name for a in archives] == [result.archive.name]  # type: ignore[union-attr]
    assert not [p for p in folder.iterdir() if p.name.startswith(".")]  # no half-written file
    raw = archives[0].read_bytes()
    assert raw.startswith(b"age-encryption.org/v1") and b"CREATE TABLE" not in raw
    other = subprocess.run(
        [str(AGE_DIR / "age-keygen")], capture_output=True, text=True, check=True
    )
    wrong = next(line for line in other.stdout.splitlines() if line.startswith("AGE-SECRET-KEY"))
    with pytest.raises(subprocess.CalledProcessError):
        decrypt(archives[0], wrong, tmp_path / "wrong.tar")
    with tarfile.open(decrypt(archives[0], identity, tmp_path / "plain.tar")) as tar:
        names = set(tar.getnames())
        manifest = json.loads(tar.extractfile("manifest.json").read())  # type: ignore[union-attr]
    assert {
        "database.dump",
        "openwebui.db",
        "tombstones.json",
        "code.bundle",
        "manifest.json",
    } <= names
    assert set(manifest["files"]) == names - {"manifest.json"}
    assert all(len(h) == 64 for h in manifest["files"].values())


def test_crash_mid_copy_leaves_no_archive(
    settings: Settings,
    keys,
    tmp_path: Path,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T39.AC3: a half-written archive never appears; the next run clears the leftover."""
    _identity, recipient = keys
    folder = tmp_path / "backups"
    folder.mkdir()

    def broken(source: Path, target: Path) -> None:
        target.write_bytes(source.read_bytes()[:100])
        raise OSError("disk full")

    working = backup._copy
    monkeypatch.setattr(backup, "_copy", broken)
    assert backup.run(settings, recipient, folder, services=backup.NoServices()).state == "failed"
    assert backup.archives(folder) == []
    monkeypatch.setattr(backup, "_copy", working)  # only the copy; the signing key stays a stub
    assert (
        backup.run(settings, recipient, folder, services=backup.NoServices()).state == "completed"
    )
    assert len(backup.archives(folder)) == 1 and not list(folder.glob(".*"))


def test_drive_unavailable_keeps_a_local_archive(settings: Settings, keys, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """T39.AC4."""
    _identity, recipient = keys
    result = backup.run(settings, recipient, tmp_path / "missing", services=backup.NoServices())
    assert result.state == "completed" and result.local_only
    assert (
        result.archive is not None and result.archive.parent == settings.data_dir / "backups-local"
    )
    assert any("Drive" in w for w in result.warnings)


class Recorder(backup.NoServices):
    def __init__(self, running: bool) -> None:
        self.running, self.calls = running, []

    def webui_running(self) -> bool:
        return self.running

    def stop_webui(self) -> None:
        self.calls.append("stop")
        self.running = False

    def start_webui(self) -> None:
        self.calls.append("start")
        self.running = True


@pytest.mark.parametrize("running", [True, False])
def test_services_end_as_they_began(
    settings: Settings, keys, tmp_path: Path, running: bool
) -> None:  # type: ignore[no-untyped-def]
    """T39.AC5, including a crashed backup noticed at the next start."""
    _identity, recipient = keys
    services = Recorder(running)
    backup.run(settings, recipient, tmp_path, services=services)
    assert services.running == running
    assert services.calls == (["stop", "start"] if running else [])
    coordinator.write_status(
        settings.data_dir, **{"backup": {"state": "running", "webui_was_running": True}}
    )
    crashed = Recorder(False)
    message = backup.recover(settings, crashed)
    assert crashed.running and "failed" in message


def test_backup_and_update_share_the_lock(settings: Settings, keys, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """T39.AC6: a backup during an update asks it to stop and waits; then it runs."""
    _identity, recipient = keys
    lock = coordinator.IndexLock(settings.data_dir / coordinator.LOCK_FILE)
    stop_file = settings.data_dir / coordinator.STOP_FILE

    def update_until_asked() -> None:
        with lock.hold("update"):
            deadline = time.monotonic() + 30
            while not stop_file.exists() and time.monotonic() < deadline:
                time.sleep(0.1)

    thread = threading.Thread(target=update_until_asked)
    thread.start()
    time.sleep(0.3)
    result = backup.run(
        settings, recipient, tmp_path, services=backup.NoServices(), wait_update=1, wait_stop=10
    )
    thread.join()
    assert result.state == "completed" and result.waited


def test_skipped_when_the_update_does_not_stop(settings: Settings, keys, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _identity, recipient = keys
    lock = coordinator.IndexLock(settings.data_dir / coordinator.LOCK_FILE)
    with lock.hold("update"):
        result = backup.run(
            settings,
            recipient,
            tmp_path,
            services=backup.NoServices(),
            wait_update=0.5,
            wait_stop=0.5,
        )
    assert result.state == "skipped" and backup.archives(tmp_path) == []


def test_a_local_archive_moves_to_drive_when_it_is_back(
    settings: Settings,
    keys,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    """T39.AC4 continued: the archive kept on the Mac is copied to Drive by the next run."""
    _identity, recipient = keys
    folder = tmp_path / "Company Brain Backups"
    first = backup.run(settings, recipient, folder, services=backup.NoServices())
    assert first.local_only
    folder.mkdir()
    second = backup.run(settings, recipient, folder, services=backup.NoServices())
    assert not second.local_only
    assert (
        len(backup.archives(folder)) == 2
        and backup.archives(settings.data_dir / "backups-local") == []
    )


def test_no_backup_is_made_before_signing_is_set_up(
    settings: Settings, keys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    """Security scan F1: an unsigned backup could never be restored, so none is made."""
    _identity, recipient = keys
    monkeypatch.setattr(signing, "stored_key", lambda: None)
    folder = tmp_path / "drive"
    folder.mkdir()
    result = backup.run(settings, recipient, folder, services=backup.NoServices())
    assert result.state == "failed" and "Set up backups" in result.reason
    assert backup.archives(folder) == []
    assert any("Set up backups" in line for line in backup.describe(settings))
