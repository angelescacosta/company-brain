"""Pointer files and unavailable files (T16).

Google Docs, Sheets and Slides appear in Drive for Desktop as small pointer
files; online-only files are "dataless" placeholders. Both are listed with a
plain-English reason and never sent to conversion. Detection uses file-system
metadata only. A document that was indexed before keeps its chunks while its
file is unavailable.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import psycopg

from company_brain.scanner import ScannedFile, ScanReport

# macOS <sys/stat.h>: the file's content is not on this Mac (File Provider placeholder).
SF_DATALESS = 0x40000000

POINTER_REASON = (
    "Google {kind} file: open it in Google Drive and save a copy as {fmt} to include it."
)
POINTER_KINDS = {
    ".gdoc": ("Docs", "Word or PDF"),
    ".gsheet": ("Sheets", "Excel"),
    ".gslides": ("Slides", "PowerPoint or PDF"),
}
ONLINE_ONLY_REASON = (
    "Online-only in Google Drive: right-click the folder in Finder and choose Available offline."
)
UNREADABLE_REASON = "Cannot be read on this Mac (no permission)."
UNSUPPORTED_REASON = "This file type is not supported."
UNLISTED_REASON = "Not in the test lists, so it was not read."


@dataclass(frozen=True)
class FileStatus:
    rel_path: str
    status: str
    reason: str
    size: int | None


def check_availability(
    report: ScanReport, stat: Callable[[str], os.stat_result] = os.stat
) -> tuple[list[ScannedFile], list[FileStatus]]:
    """Split scanned files into those that can be converted and those that are listed."""
    statuses: list[FileStatus] = []
    for entry in report.pointers:
        kind, fmt = POINTER_KINDS[entry.relative.suffix.lower()]
        reason = POINTER_REASON.format(kind=kind, fmt=fmt)
        statuses.append(FileStatus(entry.relative.as_posix(), "pointer", reason, entry.size))
    for entry in report.unsupported:
        statuses.append(
            FileStatus(entry.relative.as_posix(), "unsupported", UNSUPPORTED_REASON, entry.size)
        )
    for entry in report.unlisted:
        statuses.append(
            FileStatus(entry.relative.as_posix(), "unlisted", UNLISTED_REASON, entry.size)
        )
    available: list[ScannedFile] = []
    for item in report.files:
        rel = item.relative.as_posix()
        try:
            flags = getattr(stat(str(item.path)), "st_flags", 0)
        except OSError:
            statuses.append(FileStatus(rel, "unavailable", UNREADABLE_REASON, item.size))
            continue
        if flags & SF_DATALESS:
            statuses.append(FileStatus(rel, "unavailable", ONLINE_ONLY_REASON, item.size))
        elif not os.access(item.path, os.R_OK):
            statuses.append(FileStatus(rel, "unavailable", UNREADABLE_REASON, item.size))
        else:
            available.append(item)
    return available, statuses


def apply_statuses(conn: psycopg.Connection, statuses: Iterable[FileStatus]) -> None:
    """Replace the listed statuses and mark known documents unavailable.

    Documents are never deleted here, so their chunks stay searchable.
    """
    statuses = list(statuses)
    conn.execute("DELETE FROM brain.file_status")
    for status in statuses:
        conn.execute(
            "INSERT INTO brain.file_status (rel_path, status, reason, size) "
            "VALUES (%s, %s, %s, %s)",
            (status.rel_path, status.status, status.reason, status.size),
        )
        if status.status == "unavailable":
            conn.execute(
                "UPDATE brain.documents SET availability = 'unavailable', updated_at = now() "
                "WHERE rel_path = %s",
                (status.rel_path,),
            )
