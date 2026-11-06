"""T2.AC3: every installer matched its published checksum or code signature."""

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LOG = tomllib.loads((REPO / "config" / "install_log.toml").read_text())
VERSIONS = tomllib.loads((REPO / "config" / "versions.toml").read_text())
SHA256 = re.compile(r"[0-9a-f]{64}")


def test_every_downloaded_tool_has_a_log_entry() -> None:
    downloaded = {name for name, tool in VERSIONS["tools"].items() if tool.get("installer")}
    assert downloaded == set(LOG["installer"])


@pytest.mark.parametrize("name", sorted(LOG["installer"]))
def test_entry_is_verified(name: str) -> None:
    entry = LOG["installer"][name]
    assert entry["url"].startswith("https://")
    assert entry["version"] == VERSIONS["tools"][name]["version"]
    checksum_ok = SHA256.fullmatch(entry.get("sha256", "")) is not None and entry.get(
        "sha256"
    ) == entry.get("published_sha256")
    signature_ok = bool(entry.get("team_id")) and entry.get("notarized") is True
    assert checksum_ok or signature_ok, f"{name}: neither checksum nor signature verified"
    assert entry["verified"] in {"checksum", "signature", "checksum+signature"}
    if "checksum" in entry["verified"]:
        assert checksum_ok
    if "signature" in entry["verified"]:
        assert entry.get("team_id")


def _team_id(path: Path) -> str | None:
    result = subprocess.run(
        ["codesign", "-dv", "--verbose=2", str(path)], capture_output=True, text=True
    )
    match = re.search(r"^TeamIdentifier=(\S+)$", result.stderr, re.MULTILINE)
    return match.group(1) if match else None


@pytest.mark.parametrize("name", sorted(n for n, e in LOG["installer"].items() if e.get("team_id")))
def test_installed_program_has_the_logged_signature(name: str) -> None:
    entry = LOG["installer"][name]
    installed = Path(entry["installed_path"]).expanduser()
    assert installed.exists(), f"{name} is not installed at {installed}"
    assert _team_id(installed) == entry["team_id"]
    verify = subprocess.run(["codesign", "--verify", "--strict", str(installed)], check=False)
    assert verify.returncode == 0
