"""Command line behind the launchers and `scripts/brainctl` (T6, T20U).

Commands: start, stop, health, update [--trigger launcher|after_start|nightly|manual].
Every start, stop and update goes through here, so the firewall check (T6) and
the index lock (T20U) cannot be bypassed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from company_brain import coordinator, db
from company_brain.services import (
    REPO,
    Launchd,
    Outcome,
    define,
    health,
    jobs_dir,
    listening,
    real_gate,
    start,
    stop,
)
from company_brain.settings import Settings

TRIGGERS = ("launcher", "after_start", "nightly", "manual")
BRAINCTL = REPO / "scripts" / "brainctl"


def last_index_update() -> datetime | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT max(ended_at) FROM brain.ingest_runs WHERE state = 'completed'"
        ).fetchone()
    return row[0] if row else None


def open_browser(url: str) -> None:
    subprocess.run(["/usr/bin/open", url], check=False)  # noqa: S603 - fixed program


def nightly_job(settings: Settings) -> dict[str, object]:
    """The 01:30 update; launchd runs a missed one when the Mac wakes."""
    return {
        "Label": f"{settings.launchd_prefix}.update-nightly",
        "ProgramArguments": [str(BRAINCTL), "update", "--trigger", "nightly"],
        "StartCalendarInterval": {"Hour": 1, "Minute": 30},
        "RunAtLoad": False,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "WorkingDirectory": str(REPO),
    }


def backup_job(settings: Settings) -> dict[str, object]:
    """Nightly at 02:00, retried hourly until 06:00 when skipped (a done backup exits at once)."""
    return {
        "Label": f"{settings.launchd_prefix}.backup-nightly",
        "ProgramArguments": [str(BRAINCTL), "backup", "--nightly"],
        "StartCalendarInterval": [{"Hour": hour, "Minute": 0} for hour in range(2, 7)],
        "RunAtLoad": False,
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "WorkingDirectory": str(REPO),
    }


def run_backup(settings: Settings, nightly: bool) -> int:
    from company_brain import backup
    from company_brain.drive import load_sources

    if not backup.RECIPIENT_FILE.exists():
        print("Backups are not set up yet: double-click Set up backups first.")
        return 1
    if nightly and backup.done_today(settings):
        return 0
    folder = load_sources(REPO / "config" / "sources.toml")["backups"]
    recipient = backup.RECIPIENT_FILE.read_text(encoding="utf-8").strip()
    result = backup.run(settings, recipient, folder, services=backup.RealServices(settings))
    print("\n".join(backup.describe(settings)))
    return 0 if result.state == "completed" else 1


def delete_client(settings: Settings, name: str) -> int:
    """Delete one client after the owner types its name again (T42)."""
    from company_brain import backup, client_delete, signing
    from company_brain.drive import load_sources

    if not name:
        print("Usage: brainctl delete-client CLIENT NAME")
        return 2
    with db.connect(settings) as conn:
        scopes = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT scope FROM brain.documents WHERE client = %s OR scope = %s",
                (name, name),
            )
        ]
    if len(scopes) != 1:
        print(f"No single client called {name!r} was found; nothing was deleted.")
        return 1
    answer = input(
        f"This deletes everything the assistant holds about {name}. "
        "Type the client's name again to confirm: "
    )
    if answer.strip() != name:
        print("The names did not match; nothing was deleted.")
        return 1
    recipient = (
        backup.RECIPIENT_FILE.read_text().strip() if backup.RECIPIENT_FILE.exists() else None
    )
    places = client_delete.Places(
        webui_db=settings.data_dir / "openwebui" / "webui.db",
        exports_dir=settings.exports_dir,
        runs_dir=REPO / "reports" / "runs",
        markdown_dir=settings.data_dir / "markdown",
        tombstones_file=REPO / "config" / "tombstones.json",
        backups_dir=load_sources(REPO / "config" / "sources.toml")["backups"],
        recipient=recipient,
        signing_key=signing.stored_key(),  # the list kept next to the backups is signed (F1)
    )
    services = backup.RealServices(settings)
    lock = coordinator.IndexLock(settings.data_dir / coordinator.LOCK_FILE)
    with lock.hold("delete", wait=900):
        running = services.webui_running()
        if running:
            services.stop_webui()
        try:
            with db.connect(settings) as conn:
                report = client_delete.delete_client(conn, scopes[0], places)
        finally:
            if running:
                services.start_webui()
    print(report.message)
    return 0


def open_file(path: Path) -> None:
    subprocess.run(["/usr/bin/open", str(path)], check=False)  # noqa: S603 - fixed program


def software_environment(settings: Settings) -> Any:
    from company_brain import software_update

    return software_update.Environment(
        project=REPO,
        tool="open-webui",
        check=[
            software_update.UV,
            "run",
            "pytest",
            "-q",
            "-m",
            "not slow and not destructive and not release",
        ],  # CHK5
        state_dir=settings.data_dir / "software-update",
    )


_PROBE = """
import company_brain.egress as e, company_brain.firewall as f
for family, (address, url, flag) in e.TARGETS.items():
    for child in (False, True):
        p = e.python_probe(f"python-{family}", address, url, flag, child)
        if f.probe_outcome(p) == "fail":
            print(p.name)
"""


def real_probes(phase: str) -> list[str]:
    """The blocked Python, started fresh (it may be a new interpreter after installing)."""
    from company_brain import egress

    if not egress.internet_reachable():
        return []
    done = subprocess.run(  # noqa: S603 - the project's own interpreter, fixed script
        [str(REPO / ".venv" / "bin" / "python"), "-c", _PROBE],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    return [f"{name} reached the internet ({phase})" for name in done.stdout.split()]


def real_coverage() -> list[str]:
    """LuLu's rules cover the interpreter paths as they are now (T4.AC4)."""
    from company_brain import egress
    from company_brain.services import OPEN_WEBUI_PYTHON

    return egress.lulu_problems(
        {
            "Python (AI services)": os.path.realpath(REPO / ".venv" / "bin" / "python"),
            "Open WebUI's Python": os.path.realpath(OPEN_WEBUI_PYTHON / "python"),
            "Ollama": egress.OLLAMA,
            "PostgreSQL": egress.POSTGRES,
        }
    )


def update_software(settings: Settings, launchd: Launchd) -> int:
    """Update Python packages and Open WebUI (T41). The AI is stopped first."""
    from company_brain import software_update

    print("Stopping the AI for the update...")
    coordinator.stop_safely(settings.data_dir, lambda: stop(settings, launchd))
    print("Downloading and checking the new versions. This takes about 30 minutes...")
    result = software_update.update(software_environment(settings), real_probes, real_coverage)
    if result.state == "updated":
        subprocess.run(  # noqa: S603 - fixed program; a local commit of the new lock file
            ["/usr/bin/git", "-C", str(REPO), "commit", "-q", "-m", "Software update", "uv.lock"],
            capture_output=True,
            check=False,
        )
    print(result.message)
    print("Start AI when you want to use the assistant again.")
    return 0 if result.state == "updated" else 1


def run_restore(settings: Settings) -> int:
    """Restore the newest backup (T40). The recovery key is typed into a hidden prompt."""
    import getpass

    from company_brain import backup, client_delete, restore
    from company_brain.drive import load_sources

    folder = load_sources(REPO / "config" / "sources.toml")["backups"]
    found = backup.archives(folder) or backup.archives(settings.data_dir / "backups-local")
    if not found:
        print("No backup was found in Google Drive or on this Mac. Nothing was changed.")
        return 1
    archive = found[-1]
    print(f"Newest backup: {archive.name}")
    if input("Restore it? This replaces the assistant's current data. Type yes: ").strip() != "yes":
        print("Nothing was changed.")
        return 1
    identity = getpass.getpass("Paste your recovery key (it stays hidden), then press Return: ")
    recipient = (
        backup.RECIPIENT_FILE.read_text().strip() if backup.RECIPIENT_FILE.exists() else None
    )
    places = client_delete.Places(
        webui_db=settings.data_dir / "openwebui" / "webui.db",
        exports_dir=settings.exports_dir,
        runs_dir=REPO / "reports" / "runs",
        markdown_dir=settings.data_dir / "markdown",
        tombstones_file=REPO / "config" / "tombstones.json",
        backups_dir=folder,
        recipient=recipient,
    )
    services = backup.RealServices(settings)
    result = restore.restore(settings, archive, identity, places, services=services)
    if result.state == "needs confirmation":
        print(result.message)
        if input("Type yes to restore anyway: ").strip() == "yes":
            result = restore.restore(
                settings, archive, identity, places, services=services, confirmed=True
            )
    print(result.message)
    return 0 if result.state == "restored" else 1


def run_update(settings: Settings, trigger: str) -> dict[str, object]:
    from company_brain.embedding import OLLAMA_URL_ENV
    from company_brain.indexing import index_documents

    # The model server of these settings (the real one, or the test copy under test).
    os.environ.setdefault(OLLAMA_URL_ENV, f"http://127.0.0.1:{settings.ollama_port}")

    launchd = Launchd(jobs_dir(settings))
    deps = coordinator.UpdateDeps(
        services_running=lambda: all(listening(s.port) for s in define(settings)),
        start_services=lambda: start(settings, real_gate(), launchd),
        stop_services=lambda: stop(settings, launchd),
        ingest=lambda should_stop: index_documents(settings, should_stop, trigger=trigger),
    )
    return coordinator.update(settings.data_dir, trigger, deps)


def update_after_start(settings: Settings) -> None:
    """Start the after-start update in the background, so Start AI returns at once."""
    subprocess.Popen(  # noqa: S603 - our own command
        [str(BRAINCTL), "update", "--trigger", "after_start"],
        cwd=REPO,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=dict(os.environ),
    )


def health_lines(settings: Settings) -> list[str]:
    from company_brain.indexing import listed_files

    lines = coordinator.describe(coordinator.read_status(settings.data_dir))
    try:
        listed = listed_files(settings)
    except Exception:  # noqa: BLE001 - Check health never shows a stack trace
        return lines
    unlisted = [(path, size) for status, path, size, _r in listed if status == "unlisted"]
    if unlisted:
        lines.append("Files not in the test lists (not read):")
        lines += [f"  {Path(path).name} ({size or 0:,} bytes)" for path, size in unlisted]
    others = [(status, path, reason) for status, path, _s, reason in listed if status != "unlisted"]
    lines += [f"Not read: {Path(path).name}: {reason}" for _status, path, reason in others]
    return lines


RATING = REPO / "reports" / "rating"


def run_rating(downloads: Path | None = None, ask: Callable[[str], str] = input) -> int:
    """H6: the owner scores the answers in the rating page, then the scores are unblinded (T36)."""
    from company_brain import rating

    unblinded = RATING.parent / "blind_ratings_unblinded.csv"
    if unblinded.exists():
        print("Your scores are already saved. Thank you.")
        return 0
    if not (RATING / "key.json").exists():
        print("The answers to rate are not ready yet.")
        return 1
    pack = json.loads((RATING / "key.json").read_text(encoding="utf-8"))
    key = {(qid, letter): model for qid, letter, model in pack["rows"]}
    open_file(RATING / "rate.html")
    print("The rating page is open in your browser. Score every answer, then click Save my scores.")
    for _attempt in range(3):
        ask("When you have clicked Save my scores, come back here and press Return. ")
        found = rating.latest_scores(downloads or Path.home() / "Downloads")
        if found is None:
            print("No saved scores were found in Downloads yet.")
            continue
        try:
            scores = rating.read_scores(json.loads(found.read_text(encoding="utf-8")), pack["pack"])
            rating.unblind(scores, key, pack["languages"], unblinded)
        except (rating.IncompleteRatings, ValueError) as problem:
            print(f"The scores could not be used: {problem}")
            continue
        print(f"Thank you: your {len(key)} scores are saved.")
        return 0
    print("Nothing was saved. Double-click Rate answers again when you are ready.")
    return 1


def main(argv: list[str]) -> int:
    commands = (
        "start",
        "stop",
        "health",
        "update",
        "backup",
        "delete-client",
        "restore",
        "update-software",
        "results",
        "rate",
    )
    if not argv or argv[0] not in commands:
        print("Usage: brainctl " + "|".join(commands))
        return 2
    settings = Settings.from_env()
    launchd = Launchd(jobs_dir(settings))
    outcome: Outcome
    if argv[0] == "start":
        print("Starting the AI. Checking the firewall first...")
        from company_brain import software_update

        env = software_environment(settings)
        finished = software_update.recover(env, real_probes, real_coverage)
        if "could not be undone" in finished:
            print(finished)
            return 1
        coordinator.mark_owner_session(settings.data_dir)
        outcome = start(settings, real_gate(), launchd, open_browser)
        if finished:
            outcome.messages.insert(0, finished)
        if outcome.ok:
            from company_brain import backup

            message = backup.recover(settings, backup.RealServices(settings))
            if message:
                outcome.messages.append(message)
            launchd.install(nightly_job(settings))
            launchd.install(backup_job(settings))
            update_after_start(settings)
            outcome.messages.append("The documents are being updated in the background.")
    elif argv[0] == "stop":
        print("Stopping the AI (waiting for any document update to reach a safe point)...")
        outcome = coordinator.stop_safely(settings.data_dir, lambda: stop(settings, launchd))
    elif argv[0] == "results":
        from company_brain import dashboard

        print("Checking the test results page...")
        shown = dashboard.show(dashboard.real_paths(), open_file)
        print(f"Opened {shown.name}.")
        return 0
    elif argv[0] == "rate":
        return run_rating()
    elif argv[0] == "update-software":
        return update_software(settings, launchd)
    elif argv[0] == "restore":
        return run_restore(settings)
    elif argv[0] == "delete-client":
        return delete_client(settings, " ".join(argv[1:]))
    elif argv[0] == "backup":
        print("Making a backup. Open WebUI pauses for a minute...")
        return run_backup(settings, nightly="--nightly" in argv)
    elif argv[0] == "update":
        trigger = argv[2] if len(argv) == 3 and argv[1] == "--trigger" else "launcher"
        if trigger not in TRIGGERS:
            print(f"Unknown trigger: {trigger}")
            return 2
        print("Updating the documents. This can take a few minutes...")
        status = run_update(settings, trigger)
        print(
            "\n".join(
                coordinator.describe(dict(status))
                if status.get("state") != "already updating"
                else ["The documents are already being updated."]
            )
        )
        return 0 if status.get("state") in ("completed", "already updating") else 1
    else:
        print("Checking the AI. This takes a few seconds...")
        outcome = health(settings, real_gate(), last_index_update)
        outcome.messages.extend(health_lines(settings))
        from company_brain import backup

        outcome.messages.extend(backup.describe(settings))
    print("\n".join(outcome.messages))
    return 0 if outcome.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
