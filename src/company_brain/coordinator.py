"""One coordinator for updates, backups, starts and stops (T20U).

Every update and backup holds one exclusive lock for its whole run: an
operating-system lock (`flock`) on `~/CompanyBrainData/index.lock`, which the
system releases when the holding process dies. The holder's kind, process and
start time are written into the file for diagnostics only.

Policy (`decide`): an update while an update runs reports "already updating";
an update while a backup runs waits; a backup while an update runs asks the
update to stop at the next file boundary, then waits; Stop AI while either runs
asks it to stop and waits for the lock before stopping services, so services
never stop under a holder.

An update started while the AI is stopped goes through T6's checked start and
is a temporary session: at the end the services are stopped again, unless the
owner clicked Start AI meanwhile (which makes it an owner session).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

LOCK_FILE = "index.lock"
STATUS_FILE = "update_status.json"
STOP_FILE = "update.stop"
SESSION_FILE = "session"
OUTCOMES = frozenset({"run", "waited then run", "already updating", "skipped"})


def decide(request: str, holder: str | None) -> str:
    """What a request does, given who holds the lock (None: nobody)."""
    if holder is None:
        return "run"
    if request == "update":
        return "already updating" if holder == "update" else "wait"
    if request == "backup":
        return "wait and ask to stop" if holder == "update" else "wait"
    return "wait and ask to stop"  # stop


@dataclass(frozen=True)
class Holder:
    kind: str
    pid: int
    started: str


class IndexLock:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _open(self) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)

    def holder(self) -> Holder | None:
        """Who holds the lock now, or None if it is free."""
        fd = self._open()
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                text = self.path.read_text(encoding="utf-8").strip()
                try:
                    data = json.loads(text)
                    return Holder(str(data["kind"]), int(data["pid"]), str(data["started"]))
                except (ValueError, KeyError):
                    return Holder("unknown", 0, "")
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
        finally:
            os.close(fd)

    def try_hold(self, kind: str) -> int | None:
        fd = self._open()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        record = json.dumps(
            {
                "kind": kind,
                "pid": os.getpid(),
                "started": datetime.now().isoformat(timespec="seconds"),
            }
        )
        os.ftruncate(fd, 0)
        os.pwrite(fd, record.encode(), 0)
        return fd

    @contextlib.contextmanager
    def hold(self, kind: str, wait: float = 0.0, poll: float = 0.2) -> Iterator[None]:
        deadline = time.monotonic() + wait
        fd = self.try_hold(kind)
        while fd is None:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"the index lock is busy ({kind} could not start)")
            time.sleep(poll)
            fd = self.try_hold(kind)
        try:
            yield
        finally:
            os.ftruncate(fd, 0)
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


# Status ---------------------------------------------------------------------------


def read_status(data_dir: Path) -> dict[str, Any]:
    path = data_dir / STATUS_FILE
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def write_status(data_dir: Path, **values: Any) -> dict[str, Any]:
    status = {**read_status(data_dir), **values}
    path = data_dir / STATUS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, default=str), encoding="utf-8")
    temporary.replace(path)
    return status


def recover(data_dir: Path, lock: IndexLock) -> None:
    """After a crash: a status left "running" with a free lock becomes "failed"."""
    status = read_status(data_dir)
    if status.get("state") == "running" and lock.holder() is None:
        write_status(
            data_dir,
            state="failed",
            reason="interrupted (the Mac or the update stopped)",
            ended=datetime.now().isoformat(timespec="seconds"),
        )


def describe(status: dict[str, Any]) -> list[str]:
    """Check health's lines about document updates, in plain English."""
    if not status:
        return ["Document updates: none yet."]
    state = status.get("state")
    when = status.get("ended") or status.get("started", "")
    counts = (
        f"{status.get('added', 0)} added, {status.get('changed', 0)} changed, "
        f"{status.get('removed', 0)} removed, {status.get('failed', 0)} could not be read"
    )
    lines = []
    if state == "running":
        lines.append(
            f"Updating documents now (started {status.get('started')}, {status.get('trigger')})."
        )
    elif state == "completed":
        lines.append(f"OK: documents last updated {when} ({status.get('trigger')}): {counts}.")
    elif state == "skipped":
        lines.append(f"Problem: the document update was skipped {when}: {status.get('reason')}.")
    else:
        lines.append(
            f"Problem: the last document update failed {when}: "
            f"{status.get('reason', 'unknown reason')}."
        )
    if status.get("last_success") and state != "completed":
        lines.append(f"Last successful update: {status['last_success']}.")
    return lines


# Update --------------------------------------------------------------------------


@dataclass
class UpdateDeps:
    services_running: Callable[[], bool]
    start_services: Callable[[], Any]  # T6's checked start; returns an Outcome
    stop_services: Callable[[], Any]
    ingest: Callable[[Callable[[], bool]], Any]  # (should_stop) -> RunResult


def update(
    data_dir: Path, trigger: str, deps: UpdateDeps, wait_for_backup: float = 3600.0
) -> dict[str, Any]:
    lock = IndexLock(data_dir / LOCK_FILE)
    recover(data_dir, lock)
    current = lock.holder()
    decision = decide("update", current.kind if current else None)
    if decision == "already updating":
        return {"state": "already updating", "trigger": trigger}
    try:
        with lock.hold("update", wait=wait_for_backup if decision == "wait" else 0.0):
            return _run_update(data_dir, trigger, deps)
    except TimeoutError:
        return write_status(
            data_dir,
            state="skipped",
            trigger=trigger,
            reason="a backup was still running",
            ended=datetime.now().isoformat(timespec="seconds"),
        )


def _run_update(data_dir: Path, trigger: str, deps: UpdateDeps) -> dict[str, Any]:
    started = datetime.now().isoformat(timespec="seconds")
    (data_dir / STOP_FILE).unlink(missing_ok=True)
    write_status(
        data_dir, state="running", trigger=trigger, started=started, ended=None, reason=None
    )
    temporary = False
    if not deps.services_running():
        outcome = deps.start_services()
        if not getattr(outcome, "ok", False):
            reason = "; ".join(getattr(outcome, "messages", [])) or "the AI could not start"
            return write_status(
                data_dir,
                state="skipped",
                reason=reason,
                ended=datetime.now().isoformat(timespec="seconds"),
            )
        (data_dir / SESSION_FILE).write_text("temporary", encoding="utf-8")
        temporary = True
    try:
        result = deps.ingest(lambda: (data_dir / STOP_FILE).exists())
        state = getattr(result, "state", "failed")
        stopped = bool(getattr(result, "stopped", False))
        values: dict[str, Any] = {
            "state": "completed" if state == "completed" and not stopped else "failed",
            "added": getattr(result, "added", 0),
            "changed": getattr(result, "changed", 0),
            "removed": getattr(result, "removed", 0),
            "failed": getattr(result, "failed", 0),
            "ended": datetime.now().isoformat(timespec="seconds"),
        }
        if stopped:
            values["reason"] = "stopped at the owner's request (Stop AI)"
        elif state != "completed":
            values["reason"] = "the documents folder was not available"
        if values["state"] == "completed":
            values["last_success"] = values["ended"]
        return write_status(data_dir, **values)
    except Exception as error:  # noqa: BLE001 - recorded for Check health, never a stack trace
        return write_status(
            data_dir,
            state="failed",
            reason=type(error).__name__,
            ended=datetime.now().isoformat(timespec="seconds"),
        )
    finally:
        (data_dir / STOP_FILE).unlink(missing_ok=True)
        session = data_dir / SESSION_FILE
        if temporary and session.exists() and session.read_text(encoding="utf-8") == "temporary":
            deps.stop_services()
            session.unlink(missing_ok=True)


def mark_owner_session(data_dir: Path) -> None:
    """Start AI: from now on the services stay up after any update (owner session)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / SESSION_FILE).write_text("owner", encoding="utf-8")


def stop_safely(data_dir: Path, stop_services: Callable[[], Any], wait: float = 900.0) -> Any:
    """Stop AI: ask a running update or backup to stop at a file boundary, then stop."""
    lock = IndexLock(data_dir / LOCK_FILE)
    if lock.holder() is not None:
        (data_dir / STOP_FILE).touch()
    with lock.hold("stop", wait=wait):
        (data_dir / SESSION_FILE).unlink(missing_ok=True)
        return stop_services()


# Policy simulation for the property test ------------------------------------------


@dataclass
class Simulation:
    holders: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    outcomes: list[str] = field(default_factory=list)
    services_running: bool = True
    stop_requested: bool = False
    stopped_under_holder: bool = False

    def _stop_services(self) -> None:
        if self.holders:
            self.stopped_under_holder = True
        self.services_running = False

    def step(self, event: str) -> None:
        holder = self.holders[0] if self.holders else None
        if event in ("update", "backup", "stop"):
            decision = decide(event, holder)
            if decision == "already updating":
                self.outcomes.append("already updating")
            elif decision == "run":
                self._run(event, "run")
            else:
                if decision == "wait and ask to stop":
                    self.stop_requested = True
                self.waiting.append(event)
        elif event == "start":
            self.services_running = True
        elif event in ("crash", "finish") and self.holders:
            self.holders.pop()
            self.stop_requested = False
            self._next()

    def _run(self, request: str, outcome: str) -> None:
        if request == "stop":
            self._stop_services()
        else:
            self.holders.append(request)
        self.outcomes.append(outcome)

    def _next(self) -> None:
        while self.waiting and not self.holders:
            self._run(self.waiting.pop(0), "waited then run")

    def drain(self) -> None:
        while self.holders or self.waiting:
            if self.holders:
                self.holders.pop()
            self._next()
