"""T8.AC3: big jobs refuse to start below 15 GB free."""

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from company_brain.preflight import (
    THRESHOLD_BYTES,
    InsufficientDiskSpace,
    check_free_space,
    run_big_job,
)

GB = 1_000_000_000


def test_threshold_is_15_gb() -> None:
    assert THRESHOLD_BYTES == 15 * GB


@given(st.integers(min_value=0, max_value=2 * THRESHOLD_BYTES))
def test_refusal_exactly_below_threshold(free: int) -> None:
    if free < THRESHOLD_BYTES:
        with pytest.raises(InsufficientDiskSpace):
            check_free_space(Path("/"), free_bytes=lambda _: free)
    else:
        check_free_space(Path("/"), free_bytes=lambda _: free)


def test_message_is_plain_english() -> None:
    with pytest.raises(InsufficientDiskSpace) as info:
        check_free_space(Path("/"), free_bytes=lambda _: 9_400_000_000)
    message = str(info.value)
    assert "9.4 GB free" in message
    assert "15 GB" in message
    assert "Traceback" not in message


def test_job_does_not_start_below_threshold() -> None:
    started = []
    with pytest.raises(InsufficientDiskSpace):
        run_big_job(lambda: started.append(1), Path("/"), free_bytes=lambda _: GB)
    assert started == []


def test_job_runs_above_threshold() -> None:
    assert run_big_job(lambda: 42, Path("/"), free_bytes=lambda _: 20 * GB) == 42


def test_real_disk_usage_is_read(tmp_path: Path) -> None:
    from company_brain.preflight import free_bytes_on

    assert free_bytes_on(tmp_path) > 0
