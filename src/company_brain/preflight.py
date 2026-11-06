"""Disk preflight: big jobs refuse to start below 15 GB free (T8)."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

THRESHOLD_BYTES = 15_000_000_000


class InsufficientDiskSpace(RuntimeError):
    """Not enough free disk space to start a big job."""


def free_bytes_on(path: Path) -> int:
    return shutil.disk_usage(path).free


def check_free_space(
    path: Path, free_bytes: Callable[[Path], int] = free_bytes_on, job: str = "this job"
) -> None:
    free = free_bytes(path)
    if free < THRESHOLD_BYTES:
        raise InsufficientDiskSpace(
            f"Not enough free disk space to start {job}: {free / 1e9:.1f} GB free, "
            f"at least {THRESHOLD_BYTES // 1_000_000_000} GB needed. "
            "Free up some space (for example, empty the Bin) and try again."
        )


def run_big_job[T](
    job: Callable[[], T],
    path: Path,
    free_bytes: Callable[[Path], int] = free_bytes_on,
    name: str = "this job",
) -> T:
    check_free_space(path, free_bytes, name)
    return job()
