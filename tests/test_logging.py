"""T8.AC1 and T8.AC2: structured, content-free logging."""

import io
import json
import logging
import os
import subprocess
import sys

import pytest

from company_brain.logs import LogRecordRejected, StructuredLogger, capture_third_party

PLANTED = "Acme Holdings agreed a fee of 48,500 EUR on 3 March"


def _records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_valid_record_is_written() -> None:
    stream = io.StringIO()
    StructuredLogger(stream).log("ingest_file", component="ingest", count=3, duration_s=1.5)
    (record,) = _records(stream)
    assert record["event"] == "ingest_file"
    assert record["count"] == 3
    assert "ts" in record


@pytest.mark.parametrize(
    "fields",
    [
        {"message": "hello"},
        {"component": PLANTED},
        {"count": "3"},
        {"count": True},
        {"count": 2.5},
        {"duration_s": float("nan")},
        {"duration_s": "fast"},
        {"doc_id": "not an id with spaces"},
        {"error_type": PLANTED},
    ],
)
def test_bad_records_are_rejected(fields: dict[str, object]) -> None:
    stream = io.StringIO()
    with pytest.raises(LogRecordRejected):
        StructuredLogger(stream).log("ingest_file", **fields)
    assert stream.getvalue() == ""


def test_unknown_event_is_rejected() -> None:
    with pytest.raises(LogRecordRejected):
        StructuredLogger(io.StringIO()).log(PLANTED)


def test_rejection_message_carries_no_value() -> None:
    with pytest.raises(LogRecordRejected) as info:
        StructuredLogger(io.StringIO()).log("ingest_file", component=PLANTED)
    assert PLANTED not in str(info.value)


def _fail_with_document_text() -> None:
    raise ValueError(PLANTED)


def test_exception_logged_by_type_and_location_only() -> None:
    stream = io.StringIO()
    logger = StructuredLogger(stream)
    try:
        try:
            _fail_with_document_text()
        except ValueError as inner:
            raise RuntimeError(PLANTED) from inner
    except RuntimeError as error:
        logger.log_exception(error, component="ingest")
    text = stream.getvalue()
    assert PLANTED not in text
    assert "48,500" not in text
    (record,) = _records(stream)
    assert record["error_type"] == "RuntimeError"
    assert str(record["code_location"]).startswith("test_logging.py:")


def test_third_party_output_reduced_to_types_and_counts(
    capfd: pytest.CaptureFixture[str],
) -> None:
    stream = io.StringIO()
    logger = StructuredLogger(stream)
    with capture_third_party(logger, component="docling"):
        print(PLANTED)
        print(f"ValueError: could not parse {PLANTED}", file=sys.stderr)
        os.write(2, f"low-level C output {PLANTED}\n".encode())
        logging.getLogger("docling.pipeline").error("failed on %s", PLANTED)
        try:
            raise KeyError(PLANTED)
        except KeyError:
            logging.getLogger("psycopg").exception("boom %s", PLANTED)
        subprocess.run(
            [sys.executable, "-c", f"import sys; sys.stderr.write({PLANTED!r})"], check=True
        )
    captured = capfd.readouterr()
    text = stream.getvalue()
    for output in (text, captured.out, captured.err):
        assert PLANTED not in output
        assert "Acme" not in output
    records = _records(stream)
    assert all(r["component"] == "docling" for r in records)
    types = {r.get("error_type"): r["count"] for r in records}
    assert types["ValueError"] == 1
    assert types["KeyError"] == 1
    assert sum(int(str(r["count"])) for r in records) >= 6


def test_capture_restores_streams() -> None:
    before = (sys.stdout, sys.stderr, logging.getLogger().handlers[:])
    with capture_third_party(StructuredLogger(io.StringIO()), component="ollama"):
        pass
    assert (sys.stdout, sys.stderr, logging.getLogger().handlers[:]) == before


def test_capture_logs_even_when_the_block_fails() -> None:
    stream = io.StringIO()
    with (
        pytest.raises(ZeroDivisionError),
        capture_third_party(StructuredLogger(stream), component="ragas"),
    ):
        print(PLANTED)
        _ = 1 / 0
    assert PLANTED not in stream.getvalue()
    assert _records(stream)
