"""Nightly encrypted backup (T39).

Holds the index lock (T20U) for the whole backup: if an update holds it, wait,
then ask the update to stop at the next file boundary and wait a little more;
if it is still busy, skip and say so in Check health. With the lock held,
Open WebUI is stopped briefly so the database and Open WebUI's SQLite data form
one consistent snapshot. The archive holds the database dump, Open WebUI's
data, `config/`, the sealed questions, the tombstone list and a git bundle of
the code, with a manifest of SHA-256 checksums; it is encrypted with `age` to
the public recipient only (the secret recovery key is shown to the owner once,
H7, and never kept on the Mac), written under a hidden temporary name and
renamed into `Company Brain Backups/`, so a half-written archive never appears.
Archives of the last 30 days are kept (only the newest, with a warning, if none
is that recent). If Drive is unavailable the archive stays in the data folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from company_brain import coordinator, db
from company_brain.keychain import get_password
from company_brain.services import PG_BIN
from company_brain.settings import Settings

REPO = Path(__file__).resolve().parents[2]
AGE = Path.home() / ".local" / "opt" / "age-1.3.2" / "age"
KEEP_DAYS = 30
PREFIX = "Company Brain backup "
SUFFIX = ".tar.age"
STAMP = "%Y-%m-%d %H%M%S"


@dataclass
class Result:
    state: str  # completed, failed, skipped
    archive: Path | None = None
    local_only: bool = False
    waited: bool = False
    warnings: list[str] = field(default_factory=list)
    reason: str = ""


class NoServices:
    """Services seen by the backup (tests pass fakes; the launcher passes the real ones)."""

    def webui_running(self) -> bool:
        return False

    def stop_webui(self) -> None:
        return None

    def start_webui(self) -> None:
        return None


def retention(dates: Sequence[datetime], now: datetime) -> tuple[list[datetime], bool]:
    """(dates to keep, warning): the last 30 days, or only the newest if none is that recent."""
    recent = sorted(d for d in dates if now - d <= timedelta(days=KEEP_DAYS))
    if recent:
        return recent, False
    if dates:
        return [max(dates)], True
    return [], False


def _date_of(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.name[len(PREFIX) : -len(SUFFIX)], STAMP)
    except ValueError:
        return None


def archives(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    found = [
        p
        for p in folder.iterdir()
        if p.name.startswith(PREFIX) and p.name.endswith(SUFFIX) and _date_of(p)
    ]
    return sorted(found, key=lambda p: _date_of(p) or datetime.min)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy(source: Path, target: Path) -> None:
    shutil.copyfile(source, target)


def _collect(settings: Settings, work: Path) -> dict[str, Path]:
    """The archive's members, written into `work`."""
    members: dict[str, Path] = {}
    role = db.ROLES[settings.database]
    password = get_password(f"{settings.keychain_prefix}-db", role)
    dump = work / "database.dump"
    subprocess.run(  # noqa: S603 - fixed program, arguments built here
        [
            str(PG_BIN / "pg_dump"),
            "-Fc",
            "-h",
            db.HOST,
            "-p",
            str(db.PORT),
            "-U",
            role,
            "-d",
            settings.database,
            "-f",
            str(dump),
        ],
        env={**os.environ, "PGPASSWORD": password},
        capture_output=True,
        check=True,
    )
    members["database.dump"] = dump
    webui = settings.data_dir / "openwebui" / "webui.db"
    copy = work / "openwebui.db"
    target = sqlite3.connect(copy)
    if webui.exists():
        source = sqlite3.connect(f"file:{webui}?mode=ro", uri=True)
        source.backup(target)
        source.close()
    target.close()
    members["openwebui.db"] = copy
    with db.connect(settings) as conn:
        tombstones = [row[0] for row in conn.execute("SELECT client FROM brain.tombstones")]
    from company_brain.client_delete import tombstone_id

    ids = sorted(tombstone_id(scope) for scope in tombstones)  # IDs only, never names (T42)
    (work / "tombstones.json").write_text(json.dumps({"ids": ids}))
    members["tombstones.json"] = work / "tombstones.json"
    bundle = work / "code.bundle"
    subprocess.run(  # noqa: S603 - fixed program
        ["/usr/bin/git", "-C", str(REPO), "bundle", "create", str(bundle), "--all"],
        capture_output=True,
        check=True,
    )
    members["code.bundle"] = bundle
    sealed = REPO / "eval" / "locked.json.age"
    if sealed.exists():
        members["eval/locked.json.age"] = sealed
    for path in sorted((REPO / "config").rglob("*")):
        if path.is_file():
            members[f"config/{path.relative_to(REPO / 'config').as_posix()}"] = path
    return members


def _status(settings: Settings, **values: object) -> None:
    current = coordinator.read_status(settings.data_dir).get("backup", {})
    coordinator.write_status(settings.data_dir, backup={**current, **values})


def run(
    settings: Settings,
    recipient: str,
    folder: Path,
    services: NoServices | None = None,
    wait_update: float = 1800.0,
    wait_stop: float = 300.0,
    signing_key: bytes | None = None,
) -> Result:
    from company_brain import signing

    services = services or NoServices()
    signing_key = signing_key or signing.stored_key()
    if signing_key is None:  # an unsigned backup could never be restored (security scan F1)
        reason = (
            "backups are not signed yet: double-click Set up backups and type your "
            "recovery key once"
        )
        ended = datetime.now().isoformat(timespec="seconds")
        _status(settings, state="failed", reason=reason, ended=ended)
        return Result("failed", reason=reason)
    move_local_archives(settings, folder)
    lock = coordinator.IndexLock(settings.data_dir / coordinator.LOCK_FILE)
    waited = lock.holder() is not None
    try:
        held = lock.hold("backup", wait=wait_update)
        held.__enter__()
    except TimeoutError:
        (settings.data_dir / coordinator.STOP_FILE).touch()  # ask the update to stop
        try:
            held = lock.hold("backup", wait=wait_stop)
            held.__enter__()
        except TimeoutError:
            _status(
                settings,
                state="skipped",
                ended=datetime.now().isoformat(timespec="seconds"),
                reason="an update was still running",
            )
            return Result("skipped", reason="an update was still running")
    try:
        return _backup(settings, recipient, folder, services, waited, signing_key)
    finally:
        held.__exit__(None, None, None)


def _backup(
    settings: Settings,
    recipient: str,
    folder: Path,
    services: NoServices,
    waited: bool,
    signing_key: bytes,
) -> Result:
    from company_brain import signing

    was_running = services.webui_running()
    _status(
        settings,
        state="running",
        webui_was_running=was_running,
        started=datetime.now().isoformat(timespec="seconds"),
    )
    work = settings.data_dir / ".backup-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    result = Result("failed", waited=waited)
    destination = folder
    partial: Path | None = None
    try:
        if was_running:
            services.stop_webui()
        members = _collect(settings, work)
        manifest = signing.signed(
            signing_key,
            {
                "created": datetime.now().isoformat(timespec="seconds"),
                "files": {name: _sha256(path) for name, path in members.items()},
            },
        )
        (work / "manifest.json").write_text(json.dumps(manifest, indent=1))
        plain = work / "archive.tar"
        with tarfile.open(plain, "w") as tar:
            for name, path in members.items():
                tar.add(path, arcname=name)
            tar.add(work / "manifest.json", arcname="manifest.json")
        encrypted = work / "archive.tar.age"
        subprocess.run(  # noqa: S603 - fixed program, the recipient from config
            [str(AGE), "-r", recipient, "-o", str(encrypted), str(plain)],
            capture_output=True,
            check=True,
        )
        if not encrypted.read_bytes()[:21] == b"age-encryption.org/v1":
            raise RuntimeError("the encrypted archive is not valid")
        if not (folder.is_dir() and os.access(folder, os.W_OK)):
            destination = settings.data_dir / "backups-local"
            destination.mkdir(parents=True, exist_ok=True)
            result.local_only = True
            result.warnings.append(
                "Drive's backup folder was not available; the backup was "
                "kept on this Mac and will be copied next time."
            )
        for leftover in destination.glob(".partial-*"):
            leftover.unlink()
        stamp = datetime.now()
        name = f"{PREFIX}{stamp.strftime(STAMP)}{SUFFIX}"
        while (destination / name).exists():  # never replace an existing archive
            stamp += timedelta(seconds=1)
            name = f"{PREFIX}{stamp.strftime(STAMP)}{SUFFIX}"
        partial = destination / f".partial-{name}"
        _copy(encrypted, partial)
        if _sha256(partial) != _sha256(encrypted):
            raise RuntimeError("the copied archive does not match")
        final = destination / name
        os.replace(partial, final)
        partial = None
        keep, warning = retention(
            [d for p in archives(destination) if (d := _date_of(p))], datetime.now()
        )
        for path in archives(destination):
            if _date_of(path) not in keep:
                path.unlink()
        if warning:
            result.warnings.append("Every backup is older than 30 days: backups have been failing.")
        result.state, result.archive = "completed", final
        _status(
            settings,
            state="completed",
            archive=final.name,
            local_only=result.local_only,
            ended=datetime.now().isoformat(timespec="seconds"),
            last_success=datetime.now().isoformat(timespec="seconds"),
            warnings=result.warnings,
        )
        return result
    except Exception as error:  # noqa: BLE001 - recorded for Check health
        result.reason = type(error).__name__
        _status(
            settings,
            state="failed",
            reason=result.reason,
            ended=datetime.now().isoformat(timespec="seconds"),
        )
        return result
    finally:
        if partial is not None and partial.exists():
            partial.unlink()
        shutil.rmtree(work, ignore_errors=True)
        if was_running:
            services.start_webui()


def recover(settings: Settings, services: NoServices) -> str:
    """At start: a backup left "running" crashed; restore Open WebUI and record the failure."""
    status = coordinator.read_status(settings.data_dir).get("backup", {})
    if status.get("state") != "running":
        return ""
    if status.get("webui_was_running") and not services.webui_running():
        services.start_webui()
    _status(
        settings,
        state="failed",
        reason="interrupted (the Mac or the backup stopped)",
        ended=datetime.now().isoformat(timespec="seconds"),
    )
    return "The last backup failed: it was interrupted. The services were restored."


RECIPIENT_FILE = REPO / "config" / "backup_recipient.txt"


class RealServices(NoServices):
    """Open WebUI as Start AI runs it (launchd job)."""

    def __init__(self, settings: Settings) -> None:
        from company_brain.services import Launchd, define, jobs_dir

        self._launchd = Launchd(jobs_dir(settings))
        self._webui = next(s for s in define(settings) if s.name == "openwebui")

    def webui_running(self) -> bool:
        from company_brain.services import listening

        return bool(listening(self._webui.port))

    def stop_webui(self) -> None:
        from company_brain.services import listening

        self._launchd.stop(self._webui.label)
        deadline = time.monotonic() + 30
        while listening(self._webui.port) and time.monotonic() < deadline:
            time.sleep(0.5)

    def start_webui(self) -> None:
        from company_brain.services import ready

        self._launchd.start(self._webui)
        deadline = time.monotonic() + 60
        while not ready(self._webui) and time.monotonic() < deadline:
            time.sleep(0.5)


def done_today(settings: Settings, now: datetime | None = None) -> bool:
    status = coordinator.read_status(settings.data_dir).get("backup", {})
    last = str(status.get("last_success") or "")
    return last[:10] == (now or datetime.now()).strftime("%Y-%m-%d")


def move_local_archives(settings: Settings, folder: Path) -> int:
    """Archives kept on the Mac while Drive was unavailable go to Drive once it is back."""
    local = settings.data_dir / "backups-local"
    if not (folder.is_dir() and os.access(folder, os.W_OK)):
        return 0
    moved = 0
    for path in archives(local):
        partial = folder / f".partial-{path.name}"
        _copy(path, partial)
        if _sha256(partial) == _sha256(path):
            os.replace(partial, folder / path.name)
            path.unlink()
            moved += 1
    return moved


def describe(settings: Settings) -> list[str]:
    """Check health's lines about backups, in plain English."""
    status = coordinator.read_status(settings.data_dir).get("backup", {})
    if not RECIPIENT_FILE.exists():
        return ["Backups are not set up yet (double-click Set up backups)."]
    if not status:
        return ["Backups: none yet."]
    state, when = status.get("state"), status.get("ended") or status.get("started")
    if state == "completed":
        lines = [
            f"OK: last backup {when}"
            + (" (kept on this Mac: Drive was unavailable)." if status.get("local_only") else ".")
        ]
    elif state == "skipped":
        lines = [
            f"Problem: backup skipped {when}: {status.get('reason')}. It is retried hourly "
            "until 06:00."
        ]
    elif state == "running":
        lines = [f"A backup is running (started {status.get('started')})."]
    else:
        lines = [f"Problem: the last backup failed {when}: {status.get('reason')}."]
    lines += [f"Problem: {w}" for w in status.get("warnings", []) if "older than 30" in w]
    return lines
