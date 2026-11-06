"""Security scan F1: Set up backups keeps the signing key in the Keychain. When backups were set up
before signing existed, it asks once for the recovery key and checks it against the public key."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

from company_brain import signing

REPO = Path(__file__).resolve().parents[1]
AGE_DIR = Path.home() / ".local" / "opt" / "age-1.3.2"


def script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stored: list[bytes]):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("setup", REPO / "scripts" / "setup_backup_key.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    monkeypatch.setattr(module, "RECIPIENT", tmp_path / "backup_recipient.txt")
    monkeypatch.setattr(signing, "store_key", stored.append)
    monkeypatch.setattr(signing, "stored_key", lambda: stored[-1] if stored else None)
    return module


def new_key() -> tuple[str, str]:
    out = subprocess.run([str(AGE_DIR / "age-keygen")], capture_output=True, text=True, check=True)
    identity = next(line for line in out.stdout.splitlines() if line.startswith("AGE-SECRET-KEY"))
    recipient = subprocess.run(
        [str(AGE_DIR / "age-keygen"), "-y"], input=identity + "\n", capture_output=True, text=True
    ).stdout.strip()
    return identity, recipient


def test_a_new_setup_stores_the_signing_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stored: list[bytes] = []
    module = script(monkeypatch, tmp_path, stored)
    assert module.main() == 0
    identity = next(w for w in capsys.readouterr().out.split() if w.startswith("AGE-SECRET-KEY"))
    assert stored == [signing.key_from_identity(identity)]


def test_an_earlier_setup_asks_once_for_the_recovery_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stored: list[bytes] = []
    module = script(monkeypatch, tmp_path, stored)
    identity, recipient = new_key()
    (tmp_path / "backup_recipient.txt").write_text(recipient + "\n")
    wrong, _ = new_key()
    monkeypatch.setattr(module.getpass, "getpass", lambda _prompt: wrong)
    assert module.main() == 1 and stored == []  # not the key these backups are locked with
    monkeypatch.setattr(module.getpass, "getpass", lambda _prompt: identity)
    assert module.main() == 0 and stored == [signing.key_from_identity(identity)]
    assert module.main() == 0 and len(stored) == 1  # done: nothing asked again
