"""T6: Start AI, Stop AI and Check health, with fault injection.

Each test runs test copies of the three services: launchd jobs named
com.companybrain-test.*, a scratch PostgreSQL cluster, and test ports (the
guard refuses the real names, folder and ports). The real LuLu check runs
read-only; being online and the probe results are stubbed (AC2's "simulated
by a stub"), because the live probes belong to T4's listed test.
"""

import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from company_brain import egress
from company_brain.firewall import ProbeResult
from company_brain.services import (
    PG_BIN,
    Gate,
    Launchd,
    define,
    health,
    jobs_dir,
    listening,
    real_gate,
    start,
    stop,
)
from company_brain.settings import Settings

pytestmark = pytest.mark.integration

PASSING = [ProbeResult("python-ipv4", control_ok=True, connected=False, deny_recorded=True)]
ESCAPED = [ProbeResult("python-ipv4", control_ok=True, connected=True, deny_recorded=False)]


def gate(online: bool, probes: list[ProbeResult] = PASSING) -> Gate:
    return Gate(
        static_problems=real_gate().static_problems,
        online=lambda: online,
        probes=lambda _host: probes,
    )


@pytest.fixture
def settings() -> Iterator[Settings]:
    settings = Settings.from_env()
    if not (settings.pg_data / "PG_VERSION").exists():
        subprocess.run(
            [
                str(PG_BIN / "initdb"),
                "-D",
                str(settings.pg_data),
                "-A",
                "trust",
                "--no-instructions",
            ],
            capture_output=True,
            check=True,
        )
    launchd = Launchd(jobs_dir(settings))
    yield settings
    stop(settings, launchd)


@pytest.fixture
def launchd(settings: Settings) -> Launchd:
    return Launchd(jobs_dir(settings))


def running(settings: Settings) -> dict[str, bool]:
    return {s.name: bool(listening(s.port)) for s in define(settings)}


def assert_all_on_loopback(settings: Settings) -> None:
    for service in define(settings):
        found = listening(service.port)
        assert found, f"{service.name} is not listening"
        assert {address for _c, _p, address, _port in found} == {"127.0.0.1"}, service.name


@pytest.mark.parametrize("online", [True, False], ids=["online", "offline"])
def test_start_brings_everything_up_on_loopback(
    settings: Settings, launchd: Launchd, online: bool
) -> None:
    began = time.monotonic()
    outcome = start(settings, gate(online), launchd)
    seconds = time.monotonic() - began
    assert outcome.ok, outcome.messages
    assert seconds <= 60, seconds
    assert_all_on_loopback(settings)
    print(f"cold start {'online' if online else 'offline'}: {seconds:.1f} s")


def test_start_refuses_when_a_probe_gets_out(settings: Settings, launchd: Launchd) -> None:
    outcome = start(settings, gate(True, ESCAPED), launchd)
    assert not outcome.ok
    assert "reached the internet" in " ".join(outcome.messages)
    assert not any(running(settings).values())


def test_start_refuses_when_the_probe_is_inconclusive(settings: Settings, launchd: Launchd) -> None:
    unsure = [ProbeResult("python-ipv4", control_ok=True, connected=False, deny_recorded=False)]
    outcome = start(settings, gate(True, unsure), launchd)
    assert not outcome.ok
    assert "could not confirm" in " ".join(outcome.messages)
    assert not any(running(settings).values())


def test_start_refuses_offline_when_lulu_is_stopped(
    settings: Settings, launchd: Launchd, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(egress, "lulu_running", lambda: False)
    outcome = start(settings, gate(False), launchd)
    assert not outcome.ok
    assert "LuLu firewall is not running" in " ".join(outcome.messages)
    assert not any(running(settings).values())


def test_start_refuses_offline_when_a_rule_is_missing(settings: Settings, launchd: Launchd) -> None:
    # Apple's own Python has an allow rule and no block rule on this Mac.
    unblocked = {"Python": "/Library/Developer/CommandLineTools/usr/bin/python3"}
    broken = Gate(
        static_problems=lambda: egress.lulu_problems(unblocked),
        online=lambda: False,
        probes=lambda _host: PASSING,
    )
    outcome = start(settings, broken, launchd)
    assert not outcome.ok
    assert "no rule blocks" in " ".join(outcome.messages)
    assert not any(running(settings).values())


def test_stop_leaves_nothing_running_and_can_repeat(settings: Settings, launchd: Launchd) -> None:
    assert start(settings, gate(False), launchd).ok
    assert stop(settings, launchd).ok
    assert not any(running(settings).values())
    assert not any(launchd.loaded(s.label) for s in define(settings))
    assert stop(settings, launchd).ok


def test_health_names_each_failed_service(settings: Settings, launchd: Launchd) -> None:
    last = datetime(2026, 10, 21, 9, 30, tzinfo=UTC)
    assert start(settings, gate(False), launchd).ok
    report = health(settings, gate(False), lambda: last)
    assert report.ok, report.messages
    assert "Last index update: 21 October 2026, 09:30." in report.messages
    for service in define(settings):
        if service.name == "postgresql":
            subprocess.run(
                [str(PG_BIN / "pg_ctl"), "stop", "-D", str(settings.pg_data), "-m", "fast", "-w"],
                capture_output=True,
                check=True,
            )
        launchd.stop(service.label)
        deadline = time.monotonic() + 20
        while listening(service.port) and time.monotonic() < deadline:
            time.sleep(0.5)
        report = health(settings, gate(False), lambda: last)
        assert not report.ok
        assert f"Problem: {service.title} is not running. Click Start AI." in report.messages
        others = [s for s in define(settings) if s.name != service.name]
        assert not any(f"Problem: {s.title}" in " ".join(report.messages) for s in others)
        launchd.start(service)
        deadline = time.monotonic() + 60
        while not listening(service.port) and time.monotonic() < deadline:
            time.sleep(0.5)


def test_health_reports_the_firewall_and_an_unreadable_index(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(egress, "lulu_running", lambda: False)

    def broken() -> datetime:
        raise OSError("database down")

    report = health(settings, gate(False), broken, free_bytes=lambda _p: 1_000_000_000)
    text = "\n".join(report.messages)
    assert not report.ok
    assert "LuLu firewall is not running" in text
    assert "last index update could not be read" in text
    assert "only 1 GB free" in text


def test_health_while_stopped_skips_the_live_probe(settings: Settings) -> None:
    # Found on the real Mac on 25 October: with the AI stopped, the live probe
    # failed inside `ollama list` and Check health crashed with a traceback.
    def unreachable(_host: str) -> list[ProbeResult]:
        raise subprocess.CalledProcessError(1, ["ollama", "list"])

    stopped = Gate(static_problems=lambda: [], online=lambda: True, probes=unreachable)
    report = health(settings, stopped, lambda: None, free_bytes=lambda _p: 100_000_000_000)
    assert not report.ok
    assert (
        "Problem: The AI model server (Ollama) is not running. Click Start AI." in report.messages
    )
    assert (
        "OK: the firewall rules are in place (the AI is stopped, so no live test)."
        in report.messages
    )


def test_start_refuses_when_another_program_holds_a_port(
    settings: Settings, launchd: Launchd
) -> None:
    """Security scan F4: a program squatting on a service's port is never taken for it."""
    import socket

    postgres = next(s for s in define(settings) if s.name == "postgresql")
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", postgres.port))
        squatter.listen()
        outcome = start(settings, gate(False), launchd)
        assert not outcome.ok
        assert any("another program" in m and str(postgres.port) in m for m in outcome.messages)
        assert not any(launchd.loaded(s.label) for s in define(settings))


def test_jobs_are_not_installed_for_login(settings: Settings, launchd: Launchd) -> None:
    assert start(settings, gate(False), launchd).ok
    agents = Path.home() / "Library" / "LaunchAgents"
    assert not list(agents.glob("com.companybrain*"))
    assert all((jobs_dir(settings) / f"{s.label}.plist").exists() for s in define(settings))
