"""Delete a client, and reconcile restored data with the tombstone list (T42).

Deleting removes, by ownership metadata, the client's documents and chunks in
every index, its chat bindings and Open WebUI chats, Markdown copies, exports,
per-question evaluation lines and file notes, and adds a tombstone. The
tombstone list holds IDs only (a hash of the client's scope, never the name);
it is committed to git (`config/tombstones.json`) and also written, encrypted,
as `tombstones.age` next to the backups, so it survives the loss of the Mac.
Reconciliation applies a tombstone list to restored data (T40).
Disclosed limit: the client's data stays in backups for up to 30 days.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import psycopg

from company_brain import signing

EXPORT_INDEX = "exports.json"  # export file name -> scope, written by every export
WEBUI_TABLES = ("chat_message", "chat_file", "chatidtag", "shared_chat")
AGE = Path.home() / ".local" / "opt" / "age-1.3.2" / "age"


@dataclass(frozen=True)
class Places:
    webui_db: Path
    exports_dir: Path
    runs_dir: Path
    markdown_dir: Path
    tombstones_file: Path
    backups_dir: Path
    recipient: str | None  # the backup key's public half, or None when backups are not set up
    signing_key: bytes | None = None  # signs the list kept next to the backups (signing.py)


@dataclass(frozen=True)
class Report:
    removed: dict[str, int]
    message: str


def tombstone_id(scope: str) -> str:
    return hashlib.sha256(f"company-brain:{scope}".encode()).hexdigest()[:16]


def _name(scope: str) -> str:
    return scope.split(":", 1)[-1]


def _folders(scope: str) -> list[str]:
    name = _name(scope)
    if scope.startswith("fictional:"):
        return [f"Test data (fictional)/Clients/{name}/"]
    return [f"Clients/{name}/"]


def _remove(conn: psycopg.Connection, scopes: Iterable[str], places: Places) -> dict[str, int]:
    scopes = sorted(set(scopes))
    counts = dict.fromkeys(("documents", "chats", "exports", "evaluation", "notes"), 0)
    if not scopes:
        return counts
    chats = [
        row[0]
        for row in conn.execute(
            "SELECT chat_id FROM brain.chat_bindings WHERE scope = ANY(%s)", (scopes,)
        )
    ]
    counts["documents"] = conn.execute(
        "DELETE FROM brain.documents WHERE scope = ANY(%s)", (scopes,)
    ).rowcount
    conn.execute("DELETE FROM brain.chat_bindings WHERE scope = ANY(%s)", (scopes,))
    for folder in (f for s in scopes for f in _folders(s)):
        counts["notes"] += conn.execute(
            "DELETE FROM brain.file_status WHERE rel_path LIKE %s", (folder + "%",)
        ).rowcount
    for scope in scopes:
        conn.execute(
            "INSERT INTO brain.tombstones (client) VALUES (%s) ON CONFLICT DO NOTHING", (scope,)
        )
    conn.commit()
    if chats and places.webui_db.exists():
        db = sqlite3.connect(places.webui_db)
        marks = ",".join("?" * len(chats))
        for table in WEBUI_TABLES:
            db.execute(f"DELETE FROM {table} WHERE chat_id IN ({marks})", chats)  # noqa: S608
        counts["chats"] = db.execute(f"DELETE FROM chat WHERE id IN ({marks})", chats).rowcount  # noqa: S608
        db.commit()
        db.close()
    for folder in (f for s in scopes for f in _folders(s)):
        shutil.rmtree(places.markdown_dir / folder, ignore_errors=True)
    index_path = places.exports_dir / EXPORT_INDEX
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for name, owner in list(index.items()):
            if owner in scopes:
                (places.exports_dir / name).unlink(missing_ok=True)
                del index[name]
                counts["exports"] += 1
        index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    names = {_name(s) for s in scopes}
    for path in places.runs_dir.glob("*/questions.jsonl"):
        lines = path.read_text(encoding="utf-8").splitlines()
        keep = [line for line in lines if json.loads(line).get("client") not in names]
        counts["evaluation"] += len(lines) - len(keep)
        path.write_text("".join(f"{line}\n" for line in keep), encoding="utf-8")
    return counts


def _write_tombstones(ids: set[str], places: Places) -> None:
    places.tombstones_file.parent.mkdir(parents=True, exist_ok=True)
    places.tombstones_file.write_text(json.dumps({"ids": sorted(ids)}, indent=1) + "\n")
    if places.recipient and places.signing_key and places.backups_dir.is_dir():
        # Signed, so restore can tell it from a list someone else encrypted (security scan F1).
        listing = signing.signed(places.signing_key, {"ids": sorted(ids)})
        partial = places.backups_dir / ".partial-tombstones.age"
        subprocess.run(  # noqa: S603 - fixed program, the recipient from config
            [str(AGE), "-r", places.recipient, "-o", str(partial)],
            input=json.dumps(listing).encode(),
            capture_output=True,
            check=True,
        )
        partial.replace(places.backups_dir / "tombstones.age")


def read_tombstones(places: Places) -> set[str]:
    if not places.tombstones_file.exists():
        return set()
    return set(json.loads(places.tombstones_file.read_text(encoding="utf-8"))["ids"])


def delete_client(conn: psycopg.Connection, scope: str, places: Places) -> Report:
    removed = _remove(conn, [scope], places)
    _write_tombstones(read_tombstones(places) | {tombstone_id(scope)}, places)
    folder = _folders(scope)[0]
    message = (
        f"{_name(scope)} was deleted from the assistant ({removed['documents']} documents, "
        f"{removed['chats']} chats, {removed['exports']} exports). Now move or delete the "
        f'folder "{folder.rstrip("/")}" in Google Drive, or it stays there (the assistant '
        "will not read it again). Backups keep it for up to 30 days."
    )
    return Report(removed, message)


def reconcile(conn: psycopg.Connection, ids: set[str], places: Places) -> dict[str, int]:
    """Remove every record of every client whose tombstone ID is in `ids` (after a restore)."""
    candidates: set[str] = set()
    candidates |= {row[0] for row in conn.execute("SELECT DISTINCT scope FROM brain.documents")}
    candidates |= {row[0] for row in conn.execute("SELECT DISTINCT scope FROM brain.chat_bindings")}
    for (path,) in conn.execute("SELECT rel_path FROM brain.file_status"):
        parts = path.split("/")
        if parts[:2] == ["Test data (fictional)", "Clients"] and len(parts) > 2:
            candidates.add(f"fictional:{parts[2]}")
        elif parts[0] == "Clients" and len(parts) > 1:
            candidates.add(parts[1])
    conn.commit()
    index_path = places.exports_dir / EXPORT_INDEX
    if index_path.exists():
        candidates |= set(json.loads(index_path.read_text(encoding="utf-8")).values())
    for path in places.runs_dir.glob("*/questions.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            client = json.loads(line).get("client")
            if client:
                candidates |= {client, f"fictional:{client}"}
    return _remove(conn, [s for s in candidates if tombstone_id(s) in ids], places)
