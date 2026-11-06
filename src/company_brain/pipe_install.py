"""Install the Company Brain Pipe into Open WebUI (T29).

Writes the Function straight into Open WebUI's database while Open WebUI is
stopped (so no admin password is needed): the Pipe's source, active, owned by
the single admin account, with the assistant's address and token as valves.
Re-running replaces it with the current source.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from company_brain.backend import ensure_token
from company_brain.services import listening
from company_brain.settings import Settings

FUNCTION_ID = "company_brain"
SOURCE = Path(__file__).with_name("openwebui_pipe.py")


class InstallError(RuntimeError):
    pass


def install(settings: Settings) -> str:
    if listening(settings.openwebui_port):
        raise InstallError("Stop the AI first (Open WebUI must not be running while installing).")
    database = settings.data_dir / "openwebui" / "webui.db"
    if not database.exists():
        raise InstallError(
            "Open WebUI has not been started yet, so there is no account to own the Pipe."
        )
    token = ensure_token(settings.data_dir)
    valves = {
        "backend_url": f"http://127.0.0.1:{settings.backend_port}",
        "token": token,
        "timeout_seconds": 300,
    }
    meta = {
        "description": (
            "Answers and summaries from the consultancy's documents, one client per chat."
        ),
        "manifest": {"title": "Company Brain", "version": "0.1.0"},
    }
    now = int(time.time())
    with sqlite3.connect(database) as db:
        admins = db.execute("SELECT id FROM user WHERE role = 'admin'").fetchall()
        if len(admins) != 1:
            raise InstallError("Expected exactly one admin account in Open WebUI.")
        db.execute("DELETE FROM function WHERE id = ?", (FUNCTION_ID,))
        db.execute(
            "INSERT INTO function (id, user_id, name, type, content, meta, valves, is_active, "
            "is_global, updated_at, created_at) VALUES (?, ?, ?, 'pipe', ?, ?, ?, 1, 0, ?, ?)",
            (
                FUNCTION_ID,
                admins[0][0],
                "Company Brain",
                SOURCE.read_text(encoding="utf-8"),
                json.dumps(meta),
                json.dumps(valves),
                now,
                now,
            ),
        )
    database.chmod(0o600)  # the valves hold the service token (security scan F3)
    return FUNCTION_ID


def keep_only_our_pipe(database: Path, settings: Settings) -> None:
    """After a restore: remove every other Function and every Tool, and put back our Pipe's
    current source and this Mac's token (Open WebUI runs this code; security scan F1)."""
    with closing(sqlite3.connect(database)) as db, db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "function" in tables:
            db.execute("DELETE FROM function WHERE id <> ?", (FUNCTION_ID,))
            row = db.execute("SELECT valves FROM function WHERE id = ?", (FUNCTION_ID,)).fetchone()
            if row is not None:
                valves = {**json.loads(row[0] or "{}"), "token": ensure_token(settings.data_dir)}
                db.execute(
                    "UPDATE function SET content = ?, valves = ? WHERE id = ?",
                    (SOURCE.read_text(encoding="utf-8"), json.dumps(valves), FUNCTION_ID),
                )
        if "tool" in tables:
            db.execute("DELETE FROM tool")
    database.chmod(0o600)


def main() -> None:  # pragma: no cover - run by hand or by the setup script
    print(f"Installed the {install(Settings.from_env())} Pipe into Open WebUI.")


if __name__ == "__main__":
    main()
