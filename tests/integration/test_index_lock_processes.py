"""T20U: the index lock across real processes (flock on ~/CompanyBrainData/index.lock)."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from company_brain.coordinator import IndexLock

pytestmark = pytest.mark.integration

HOLD = """
import sys, time
from pathlib import Path
from company_brain.coordinator import IndexLock
lock = IndexLock(Path(sys.argv[1]))
with lock.hold("update"):
    print("held", flush=True)
    time.sleep(60)
"""


def holder(path: Path) -> subprocess.Popen[str]:
    process = subprocess.Popen(  # noqa: S603 - this test's own child
        [sys.executable, "-c", HOLD, str(path)], stdout=subprocess.PIPE, text=True
    )
    assert process.stdout is not None and process.stdout.readline().strip() == "held"
    return process


def test_one_holder_and_a_killed_holder_frees_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "index.lock"
    first = holder(path)
    lock = IndexLock(path)
    try:
        assert lock.try_hold("backup") is None  # another process holds it
        found = lock.holder()
        assert found is not None and found.kind == "update" and found.pid == first.pid
    finally:
        first.kill()
        first.wait()
    deadline = time.monotonic() + 5
    while lock.holder() is not None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert lock.holder() is None  # the operating system released it with the process
    with lock.hold("backup", wait=5):
        assert lock.holder() is not None and lock.holder().kind == "backup"  # type: ignore[union-attr]
    assert lock.holder() is None


def test_waiting_for_a_holder(tmp_path: Path) -> None:
    path = tmp_path / "index.lock"
    first = holder(path)
    lock = IndexLock(path)
    started = time.monotonic()
    with pytest.raises(TimeoutError), lock.hold("update", wait=1):
        pass
    assert time.monotonic() - started >= 1
    first.kill()
    first.wait()
    with lock.hold("update", wait=5):
        pass
