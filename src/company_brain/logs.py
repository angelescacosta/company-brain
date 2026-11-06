"""Structured, content-free logging (T8).

Only allowlisted fields with typed values are written: integers, finite
floats, enumerations and IDs matching a pattern. Nothing free-text can pass,
so document content never reaches a log that Claude may read. Exceptions are
logged by type and code location; third-party output is reduced to error
types and line counts.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TextIO


class LogRecordRejected(ValueError):
    """A record had a field that is not allowlisted or a value of the wrong type."""


@dataclass(frozen=True)
class IntField:
    pass


@dataclass(frozen=True)
class FloatField:
    pass


@dataclass(frozen=True)
class EnumField:
    values: frozenset[str]


@dataclass(frozen=True)
class IdField:
    pattern: str


_HEX_ID = IdField(r"[0-9a-f]{8,64}")

COMPONENTS = frozenset(
    {
        "backup",
        "docling",
        "eval",
        "gate",
        "ingest",
        "launcher",
        "ocrmac",
        "ollama",
        "openwebui",
        "preflight",
        "psycopg",
        "ragas",
        "rerank",
        "retrieval",
        "sentence_transformers",
        "update",
    }
)

EVENTS = frozenset(
    {
        "backup_run",
        "eval_run",
        "exception",
        "ingest_file",
        "ingest_run",
        "preflight",
        "query",
        "service_start",
        "service_stop",
        "third_party_output",
        "update_run",
    }
)

FIELDS: dict[str, IntField | FloatField | EnumField | IdField] = {
    "component": EnumField(COMPONENTS),
    "status": EnumField(frozenset({"ok", "failed", "skipped", "running", "completed"})),
    "level": EnumField(frozenset({"debug", "info", "warning", "error"})),
    "trigger": EnumField(frozenset({"launcher", "after_start", "nightly", "manual"})),
    "count": IntField(),
    "added": IntField(),
    "changed": IntField(),
    "removed": IntField(),
    "failed": IntField(),
    "bytes": IntField(),
    "duration_s": FloatField(),
    "free_gb": FloatField(),
    "error_type": IdField(r"[A-Z][A-Za-z0-9_]{0,63}"),
    "code_location": IdField(r"[A-Za-z0-9_]{1,64}\.py:[0-9]{1,6}"),
    "doc_id": _HEX_ID,
    "chunk_id": _HEX_ID,
    "run_id": _HEX_ID,
    "index_id": _HEX_ID,
}


def _valid(spec: IntField | FloatField | EnumField | IdField, value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(spec, IntField):
        return isinstance(value, int)
    if isinstance(spec, FloatField):
        return isinstance(value, (int, float)) and math.isfinite(value)
    if isinstance(spec, EnumField):
        return isinstance(value, str) and value in spec.values
    return isinstance(value, str) and re.fullmatch(spec.pattern, value) is not None


class StructuredLogger:
    """Writes one JSON object per line; rejects anything not allowlisted and typed."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def log(self, event: str, **fields: object) -> None:
        if event not in EVENTS:
            raise LogRecordRejected("event is not on the allowlist")
        unknown = sum(1 for name in fields if name not in FIELDS)
        if unknown:
            raise LogRecordRejected(f"{unknown} field(s) not on the allowlist")
        bad = sorted(name for name, value in fields.items() if not _valid(FIELDS[name], value))
        if bad:
            raise LogRecordRejected(f"wrong type or value for field(s) {bad}")
        record: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "event": event,
        }
        record.update(fields)
        self._stream.write(json.dumps(record, sort_keys=True) + "\n")
        self._stream.flush()

    def log_exception(self, error: BaseException, component: str) -> None:
        """Log an exception by type and innermost code location, never its text."""
        fields: dict[str, object] = {
            "component": component,
            "error_type": type(error).__name__,
        }
        traceback = error.__traceback__
        if traceback is not None:
            while traceback.tb_next is not None:
                traceback = traceback.tb_next
            filename = os.path.basename(traceback.tb_frame.f_code.co_filename)
            location = f"{filename}:{traceback.tb_lineno}"
            if _valid(FIELDS["code_location"], location):
                fields["code_location"] = location
        if not _valid(FIELDS["error_type"], fields["error_type"]):
            fields["error_type"] = "Exception"
        self.log("exception", **fields)


_ERROR_TYPE = re.compile(r"\b([A-Z][A-Za-z0-9_]{0,50}(?:Error|Exception|Warning|Exit))\b")


class _CountingHandler(logging.Handler):
    """Counts log records by exception type; never formats the message."""

    def __init__(self, counts: Counter[str | None]) -> None:
        super().__init__(level=logging.NOTSET)
        self._counts = counts

    def emit(self, record: logging.LogRecord) -> None:
        exc_type = record.exc_info[0] if record.exc_info else None
        self._counts[exc_type.__name__ if exc_type is not None else None] += 1


def _count_lines(text: str, counts: Counter[str | None]) -> None:
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _ERROR_TYPE.search(line)
        counts[match.group(1) if match else None] += 1


@contextmanager
def capture_third_party(logger: StructuredLogger, component: str) -> Iterator[None]:
    """Capture Python and C-level stdout and stderr, and Python logging, in the block.

    At the end, only the error types found and the line counts are logged.
    """
    counts: Counter[str | None] = Counter()
    sys.stdout.flush()
    sys.stderr.flush()
    saved_streams = (sys.stdout, sys.stderr)
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    python_out, python_err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryFile() as fd_out, tempfile.TemporaryFile() as fd_err:
        saved_fds = (os.dup(1), os.dup(2))
        try:
            os.dup2(fd_out.fileno(), 1)
            os.dup2(fd_err.fileno(), 2)
            sys.stdout, sys.stderr = python_out, python_err
            root.handlers[:] = [_CountingHandler(counts)]
            yield
        finally:
            root.handlers[:] = saved_handlers
            sys.stdout, sys.stderr = saved_streams
            os.dup2(saved_fds[0], 1)
            os.dup2(saved_fds[1], 2)
            os.close(saved_fds[0])
            os.close(saved_fds[1])
            for captured in (fd_out, fd_err):
                captured.seek(0)
                _count_lines(captured.read().decode("utf-8", "replace"), counts)
            _count_lines(python_out.getvalue(), counts)
            _count_lines(python_err.getvalue(), counts)
            for error_type, count in sorted(counts.items(), key=lambda item: item[0] or ""):
                fields: dict[str, object] = {"component": component, "count": count}
                if error_type is not None and _valid(FIELDS["error_type"], error_type):
                    fields["error_type"] = error_type
                logger.log("third_party_output", **fields)
