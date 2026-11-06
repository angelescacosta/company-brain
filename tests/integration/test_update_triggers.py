"""T20U: the three entry points, the checked start, status after every outcome, and
simultaneous triggers (services and ingestion are fakes; the lock is real)."""

import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from company_brain import coordinator
from company_brain.coordinator import UpdateDeps, read_status, update

pytestmark = pytest.mark.integration


@dataclass
class Result:
    state: str = "completed"
    added: int = 1
    changed: int = 0
    removed: int = 0
    failed: int = 0
    stopped: bool = False


@dataclass
class Fakes:
    running: bool = True
    start_ok: bool = True
    starts: int = 0
    stops: int = 0
    result: Result | None = None
    slow: float = 0.0

    def deps(self) -> UpdateDeps:
        def start():  # type: ignore[no-untyped-def]
            self.starts += 1
            self.running = self.start_ok
            return type(
                "Outcome",
                (),
                {"ok": self.start_ok, "messages": [] if self.start_ok else ["firewall not set up"]},
            )()

        def stop():  # type: ignore[no-untyped-def]
            self.stops += 1
            self.running = False

        def ingest(should_stop):  # type: ignore[no-untyped-def]
            deadline = time.monotonic() + self.slow
            while time.monotonic() < deadline:
                if should_stop():
                    return Result(stopped=True)
                time.sleep(0.05)
            return self.result or Result()

        return UpdateDeps(lambda: self.running, start, stop, ingest)


@pytest.mark.parametrize("trigger", ["launcher", "after_start", "nightly"])
def test_every_entry_point_records_its_trigger(tmp_path: Path, trigger: str) -> None:
    """T20U.AC1."""
    status = update(tmp_path, trigger, Fakes().deps())
    assert status["state"] == "completed" and read_status(tmp_path)["trigger"] == trigger


def test_stopped_services_go_through_the_checked_start_and_stop_again(tmp_path: Path) -> None:
    """T20U.AC5: a temporary session ends with the services stopped; a refused start skips."""
    fakes = Fakes(running=False)
    assert update(tmp_path, "nightly", fakes.deps())["state"] == "completed"
    assert fakes.starts == 1 and fakes.stops == 1
    refused = Fakes(running=False, start_ok=False)
    status = update(tmp_path, "launcher", refused.deps())
    assert status["state"] == "skipped" and "firewall" in status["reason"] and refused.stops == 0


def test_status_after_each_outcome(tmp_path: Path) -> None:
    """T20U.AC6."""
    partial = update(tmp_path, "launcher", Fakes(result=Result(failed=2)).deps())
    assert partial["state"] == "completed" and partial["failed"] == 2
    missing = update(tmp_path, "launcher", Fakes(result=Result(state="failed")).deps())
    assert missing["state"] == "failed" and "not available" in missing["reason"]
    assert read_status(tmp_path)["last_success"] == partial["last_success"]
    coordinator.write_status(tmp_path, state="running", started="2026-10-24T10:00:00")
    coordinator.recover(tmp_path, coordinator.IndexLock(tmp_path / coordinator.LOCK_FILE))
    crashed = read_status(tmp_path)
    assert crashed["state"] == "failed" and "interrupted" in crashed["reason"]
    assert coordinator.IndexLock(tmp_path / coordinator.LOCK_FILE).holder() is None


HOLD = """
import sys, time
from pathlib import Path
from company_brain.coordinator import IndexLock
with IndexLock(Path(sys.argv[1])).hold(sys.argv[2]):
    print("held", flush=True)
    time.sleep(float(sys.argv[3]))
"""


def hold(tmp_path: Path, kind: str, seconds: float) -> subprocess.Popen[str]:
    process = subprocess.Popen(  # noqa: S603 - this test's own child
        [sys.executable, "-c", HOLD, str(tmp_path / coordinator.LOCK_FILE), kind, str(seconds)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None and process.stdout.readline().strip() == "held"
    return process


def test_never_two_updates_and_never_during_a_backup(tmp_path: Path) -> None:
    """T20U.AC2."""
    other = hold(tmp_path, "update", 30)
    try:
        assert update(tmp_path, "nightly", Fakes().deps())["state"] == "already updating"
    finally:
        other.kill()
        other.wait()
    backup = hold(tmp_path, "backup", 2)
    started = time.monotonic()
    status = update(tmp_path, "launcher", Fakes().deps())
    assert status["state"] == "completed" and time.monotonic() - started >= 1.5  # waited
    backup.wait()


def test_simultaneous_triggers(tmp_path: Path) -> None:
    """T20U.AC7: launcher with after-start; Start AI during a nightly run; Stop AI mid-update."""
    slow = Fakes(slow=3)
    results: list[dict] = []  # type: ignore[type-arg]
    first = threading.Thread(
        target=lambda: results.append(update(tmp_path, "after_start", slow.deps()))
    )
    first.start()
    time.sleep(0.5)
    assert update(tmp_path, "launcher", Fakes().deps())["state"] == "already updating"
    first.join()
    assert results[0]["trigger"] == "after_start"

    nightly = Fakes(running=False, slow=2)
    thread = threading.Thread(target=lambda: update(tmp_path, "nightly", nightly.deps()))
    thread.start()
    time.sleep(0.5)
    coordinator.mark_owner_session(tmp_path)  # the owner clicks Start AI meanwhile
    thread.join()
    assert nightly.stops == 0  # an owner session stays up

    long = Fakes(slow=30)
    thread = threading.Thread(target=lambda: update(tmp_path, "launcher", long.deps()))
    thread.start()
    time.sleep(0.5)
    stops: list[str] = []
    coordinator.stop_safely(tmp_path, lambda: stops.append("stopped"), wait=10)
    thread.join(timeout=5)
    assert stops == ["stopped"] and not thread.is_alive()
    assert "Stop AI" in read_status(tmp_path)["reason"]


def test_real_start_returns_fast_and_updates_in_the_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T20U.AC3 and AC4 with the real launcher and the test copies of the services."""
    from company_brain import launcher
    from company_brain.services import Gate, Launchd, jobs_dir, real_gate
    from company_brain.services import stop as stop_services
    from company_brain.settings import Settings

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from owui_harness import init_cluster

    settings = Settings.from_env()
    init_cluster(settings)
    assert settings.root is not None
    unlisted = settings.root / "Clients" / "Newco" / "Reports" / "Unlisted report.pdf"
    unlisted.parent.mkdir(parents=True, exist_ok=True)
    unlisted.write_bytes(b"%PDF-1.4 not in the test lists")
    offline = Gate(
        static_problems=real_gate().static_problems, online=lambda: False, probes=lambda _h: []
    )
    monkeypatch.setattr(launcher, "real_gate", lambda: offline)
    monkeypatch.setattr(launcher, "open_browser", lambda _url: None)
    launchd = Launchd(jobs_dir(settings))
    try:
        began = time.monotonic()
        assert launcher.main(["start"]) == 0
        assert time.monotonic() - began < 60  # T6.AC1 still holds
        deadline = time.monotonic() + 300
        status: dict = {}  # type: ignore[type-arg]
        while time.monotonic() < deadline:
            status = read_status(settings.data_dir)
            if status.get("trigger") == "after_start" and status.get("state") in (
                "completed",
                "failed",
            ):
                break
            time.sleep(2)
        assert status.get("trigger") == "after_start" and status.get("state") == "completed", status
        assert launchd.loaded(f"{settings.launchd_prefix}.update-nightly")
        lines = launcher.health_lines(settings)
        assert any("Unlisted report.pdf" in line and "bytes" in line for line in lines), lines
    finally:
        launchd.stop(f"{settings.launchd_prefix}.update-nightly")
        stop_services(settings, launchd)
