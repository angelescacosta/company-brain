"""Restore a backup on this Mac (T40).

The archive is decrypted with the recovery key (typed by the owner into a hidden
prompt; Claude only sees pass or fail), unpacked into a work folder and checked
against its manifest before anything changes. A wrong key or a damaged archive
stops there. The newest tombstone list (the committed `config/tombstones.json`
and the encrypted copy next to the backups; the archive's own list is applied
too but may predate the last deletion) is required: without one, restore stops
and asks the owner to confirm.

The assistant's database role may not create databases or rename schemas, so
the switch-over works on the same schema: a safety copy of the current data is
taken first (a dump of the `brain` schema and a copy of Open WebUI's data); the
archive's tables then replace the current ones in one transaction; the
deletion reconciliation (T42) runs; the result is checked. If any of this
fails, the safety copy is put back, so the previous data stays in use.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from company_brain import backup, client_delete, coordinator, db, pipe_install, signing
from company_brain.keychain import get_password
from company_brain.services import PG_BIN
from company_brain.settings import Settings

MEMBERS = {"database.dump", "openwebui.db", "tombstones.json", "code.bundle", "manifest.json"}
NOTHING_CHANGED = "Nothing was changed."
_TOC = re.compile(r"^\d+; \d+ \d+ ((?:[A-Z]+ )+)(\S+) ")


@dataclass(frozen=True)
class Result:
    state: str  # restored, failed, needs confirmation
    message: str


def _pg(settings: Settings) -> tuple[list[str], dict[str, str]]:
    role = db.ROLES[settings.database]
    password = get_password(f"{settings.keychain_prefix}-db", role)
    args = ["-h", db.HOST, "-p", str(db.PORT), "-U", role, "-d", settings.database]
    return args, {**os.environ, "PGPASSWORD": password}


def _decrypt(path: Path, identity: str, out: Path) -> bool:
    done = subprocess.run(  # noqa: S603 - fixed program; the key goes through a pipe
        [str(backup.AGE), "-d", "-i", "/dev/stdin", "-o", str(out), str(path)],
        input=(identity.strip() + "\n").encode(),
        capture_output=True,
        check=False,
    )
    return done.returncode == 0


def _unpack(archive: Path, identity: str, work: Path) -> Path | None:
    """The checked contents of the archive, or None if it cannot be opened or is damaged."""
    plain = work / "archive.tar"
    if not _decrypt(archive, identity, plain):
        return None
    target = work / "archive"
    try:
        with tarfile.open(plain) as tar:
            members = tar.getmembers()
            for member in members:
                name = member.name
                if not member.isfile() or name.startswith("/") or ".." in Path(name).parts:
                    return None
                if name not in MEMBERS and not name.startswith(("config/", "eval/")):
                    return None
            tar.extractall(target, filter="data")
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    except (tarfile.TarError, OSError, ValueError):
        return None
    files = manifest.get("files", {})
    on_disk = {
        p.relative_to(target).as_posix()
        for p in target.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    if set(files) != on_disk or not {"database.dump", "openwebui.db"} <= on_disk:
        return None
    if any(backup._sha256(target / name) != digest for name, digest in files.items()):
        return None
    return target


def _ids(data: dict[str, object]) -> set[str]:
    if "ids" in data:
        return {str(i) for i in data["ids"]}  # type: ignore[attr-defined]
    return {client_delete.tombstone_id(str(s)) for s in data.get("clients", [])}  # type: ignore[attr-defined]


def tombstones(places: client_delete.Places, identity: str, work: Path) -> set[str] | None:
    """The newest tombstone list (union of the copies found), or None if none is found."""
    found: set[str] | None = None
    if places.tombstones_file.exists():
        found = _ids(json.loads(places.tombstones_file.read_text(encoding="utf-8")))
    sealed = places.backups_dir / "tombstones.age"
    if sealed.exists() and _decrypt(sealed, identity, work / "tombstones.json"):
        listing = json.loads((work / "tombstones.json").read_text())
        # Anyone can encrypt a list to the public key: only a signed one counts (scan F1).
        if signing.verified(signing.key_from_identity(identity), listing):
            found = (found or set()) | _ids(listing)
    return found


def _brain_only(settings: Settings, dump: Path, work: Path) -> Path:
    """A restore list with the `brain` schema's objects but not the schema itself."""
    listing = subprocess.run(  # noqa: S603 - fixed program
        [str(PG_BIN / "pg_restore"), "-l", str(dump)], capture_output=True, text=True, check=True
    ).stdout
    keep = []
    for line in listing.splitlines():
        match = _TOC.match(line)
        if match and match.group(2) == "brain" and match.group(1).strip() != "SCHEMA":
            keep.append(line)
    path = work / f"{dump.stem}.list"
    path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    return path


def _load(settings: Settings, dump: Path, work: Path) -> None:
    """Replace the `brain` tables with the dump's, in one transaction (all or nothing)."""
    args, env = _pg(settings)
    subprocess.run(  # noqa: S603 - fixed program
        [
            str(PG_BIN / "pg_restore"),
            *args,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "--single-transaction",
            "--exit-on-error",
            "-L",
            str(_brain_only(settings, dump, work)),
            str(dump),
        ],
        env=env,
        capture_output=True,
        check=True,
    )


def _safety_dump(settings: Settings, out: Path) -> None:
    args, env = _pg(settings)
    subprocess.run(  # noqa: S603 - fixed program
        [str(PG_BIN / "pg_dump"), "-Fc", "-n", "brain", *args, "-f", str(out)],
        env=env,
        capture_output=True,
        check=True,
    )


def _sqlite_ok(path: Path) -> bool:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return bool(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    finally:
        conn.close()


def restore(
    settings: Settings,
    archive: Path,
    identity: str,
    places: client_delete.Places,
    services: backup.NoServices | None = None,
    confirmed: bool = False,
    before_switch: Callable[[], None] | None = None,
) -> Result:
    services = services or backup.NoServices()
    work = settings.data_dir / ".restore-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        contents = _unpack(archive, identity, work)
        if contents is None:
            return Result(
                "failed",
                "The recovery key did not open this backup, or the backup is damaged. "
                + NOTHING_CHANGED,
            )
        manifest = json.loads((contents / "manifest.json").read_text(encoding="utf-8"))
        if not signing.verified(signing.key_from_identity(identity), manifest):
            # It opens with the recovery key, but anyone can encrypt to the public key.
            return Result(
                "failed",
                "This backup was not made by this assistant: its signature does not match "
                "your recovery key. " + NOTHING_CHANGED,
            )
        ids = tombstones(places, identity, work)
        if ids is None and not confirmed:
            return Result(
                "needs confirmation",
                "No list of deleted clients was found (neither on this Mac nor next to the "
                "backups), so a client deleted after this backup could come back. Please "
                "confirm that you want to restore anyway. " + NOTHING_CHANGED,
            )
        archived = json.loads((contents / "tombstones.json").read_text(encoding="utf-8"))
        ids = (ids or set()) | _ids(archived)
        lock = coordinator.IndexLock(settings.data_dir / coordinator.LOCK_FILE)
        with lock.hold("restore", wait=900):
            return _switch(settings, contents, ids, places, services, work, before_switch)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _switch(
    settings: Settings,
    contents: Path,
    ids: set[str],
    places: client_delete.Places,
    services: backup.NoServices,
    work: Path,
    before_switch: Callable[[], None] | None,
) -> Result:
    running = services.webui_running()
    if running:
        services.stop_webui()
    webui = settings.data_dir / "openwebui" / "webui.db"
    previous_webui = work / "previous-webui.db"
    had_webui = webui.exists()
    previous_dump = work / "previous.dump"
    try:
        _safety_dump(settings, previous_dump)
        if had_webui:
            shutil.copyfile(webui, previous_webui)
        try:
            _load(settings, contents / "database.dump", work)
            with db.connect(settings) as conn:
                db.migrate(conn)
            webui.parent.mkdir(parents=True, exist_ok=True)
            staged = webui.with_name(".restored-webui.db")
            shutil.copyfile(contents / "openwebui.db", staged)
            os.replace(staged, webui)
            # Open WebUI runs the code stored in its database: keep only our Pipe (scan F1).
            pipe_install.keep_only_our_pipe(webui, settings)
            with db.connect(settings) as conn:
                client_delete.reconcile(conn, ids, places)
                conn.execute("SELECT count(*) FROM brain.documents").fetchone()
            if not _sqlite_ok(webui):
                raise RuntimeError("Open WebUI's restored data is not readable")
            if before_switch is not None:
                before_switch()
        except Exception as error:  # noqa: BLE001 - the previous data is put back
            _load(settings, previous_dump, work)
            if had_webui:
                os.replace(previous_webui, webui)
            else:
                webui.unlink(missing_ok=True)
            return Result(
                "failed",
                f"The restore did not finish ({type(error).__name__}); your previous data is "
                "still in use.",
            )
        sealed = contents / "eval" / "locked.json.age"
        target = backup.REPO / "eval" / "locked.json.age"
        if sealed.exists() and not target.exists():
            shutil.copyfile(sealed, target)
        return Result("restored", "The backup was restored and checked.")
    finally:
        if running:
            services.start_webui()
