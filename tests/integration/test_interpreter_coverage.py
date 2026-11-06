"""T4.AC4: every program the services run as is blocked by a LuLu rule, Ollama and
PostgreSQL by their signing team as well, and LuLu's pinned settings hold.
Reads LuLu's rule and settings files and the running processes (read-only)."""

import os
import plistlib
import subprocess
import tomllib
from pathlib import Path

import pytest

from company_brain.firewall import (
    LULU_PREFERENCES,
    LULU_RULES,
    SERVICE_PORTS,
    coverage_problems,
    listeners,
    lulu_rules,
    settings_problems,
    team_of,
)

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
OPEN_WEBUI_PYTHON = Path.home() / ".local/share/uv/tools/open-webui/bin/python"


def executable_of(pid: int) -> str:
    output = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "txt", "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    first = next(line[1:] for line in output.splitlines() if line.startswith("n"))
    return os.path.realpath(first)


def service_executables() -> dict[str, str]:
    found = {
        "project python": os.path.realpath(REPO / ".venv" / "bin" / "python"),
        "open-webui python": os.path.realpath(OPEN_WEBUI_PYTHON),
    }
    output = subprocess.run(
        ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], capture_output=True, text=True, check=False
    ).stdout
    for service, (port, _prefix) in SERVICE_PORTS.items():
        pids = {pid for _cmd, pid, _addr, p in listeners(output) if p == port}
        assert pids, f"{service} is not running"
        for pid in sorted(pids):
            found[f"{service} (pid {pid})"] = executable_of(pid)
    return found


def test_every_service_program_is_blocked() -> None:
    executables = service_executables()
    assert coverage_problems(executables, lulu_rules(LULU_RULES.read_bytes())) == []


def test_signed_services_are_blocked_by_their_team() -> None:
    log = tomllib.loads((REPO / "config" / "install_log.toml").read_text())["installer"]
    expected = {"ollama": log["ollama"]["team_id"], "postgres": log["postgres_app"]["team_id"]}
    executables = service_executables()
    rules = lulu_rules(LULU_RULES.read_bytes())
    for name, team in expected.items():
        path = next(p for k, p in executables.items() if k.startswith(name))
        teams = {team_of(r) for r in rules if r.path == path}
        assert teams == {team}, f"{name}: rule signer {teams}, installed from {team}"


def test_pinned_settings() -> None:
    preferences = plistlib.loads(LULU_PREFERENCES.read_bytes())
    assert settings_problems(preferences) == []
