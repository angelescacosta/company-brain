"""Pytest plugin: build the test environment and guard every test (T1).

At session start it refuses to run if the inherited environment names a real
resource, then creates a temporary root, data and export folder, selects the
test database, Keychain prefix and ports, exports them for subprocesses, and
activates the guard. Each test is guarded by its node ID.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import tomllib
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

import pytest

from company_brain import isolation
from company_brain.settings import Settings

ALLOWLIST = Path("config") / "real_resource_allowlist.toml"
SOURCES = Path("config") / "sources.toml"


@dataclass(frozen=True)
class TestEnvironment:
    base: Path
    root: Path
    data_dir: Path
    exports_dir: Path

    def variables(self) -> dict[str, str]:
        return {
            isolation.ENV_TEST_FLAG: "1",
            isolation.ENV_ROOT: str(self.root),
            isolation.ENV_DATA: str(self.data_dir),
            isolation.ENV_EXPORTS: str(self.exports_dir),
            isolation.ENV_DB: isolation.TEST_DATABASE,
            isolation.ENV_KEYCHAIN: isolation.TEST_KEYCHAIN_PREFIX,
            isolation.ENV_OPENWEBUI_PORT: str(isolation.TEST_PORTS["openwebui"]),
            isolation.ENV_BACKEND_PORT: str(isolation.TEST_PORTS["backend"]),
            isolation.ENV_OLLAMA_PORT: str(isolation.TEST_SERVICE_PORTS["ollama"]),
            isolation.ENV_PG_PORT: str(isolation.TEST_SERVICE_PORTS["postgresql"]),
            isolation.ENV_PG_DATA: str(self.base / "pg"),
            isolation.ENV_LAUNCHD: isolation.TEST_LAUNCHD_PREFIX,
        }


_ENVIRONMENT_KEY = pytest.StashKey[TestEnvironment]()


def _real_drive_roots(rootpath: Path) -> list[Path]:
    """The Drive root recorded by T10, if any, is a real resource too."""
    sources = rootpath / SOURCES
    if not sources.exists():
        return []
    data = tomllib.loads(sources.read_text(encoding="utf-8"))
    root = data.get("root", {}).get("path")
    return [Path(root)] if root else []


def _refuse(reason: str) -> None:
    pytest.exit(f"Test environment refused: {reason}", returncode=3)


def pytest_sessionstart(session: pytest.Session) -> None:
    rootpath = session.config.rootpath
    resources = isolation.RealResources.for_home(Path.home(), _real_drive_roots(rootpath))
    guard = isolation.Guard(
        resources=resources, allowlist=isolation.load_allowlist(rootpath / ALLOWLIST)
    )
    for key, value in os.environ.items():
        if key == isolation.ENV_GRANT:
            _refuse(f"{key} is set before the session; grants come only from real_resources()")
        kinds = resources.kinds_in_text(value)
        if kinds:
            _refuse(f"environment variable {key} points at a real resource ({sorted(kinds)})")

    base = Path(tempfile.mkdtemp(prefix="cb-test-"))
    environment = TestEnvironment(
        base=base,
        root=base / "root",
        data_dir=base / "data",
        exports_dir=base / "exports",
    )
    for folder in (environment.root, environment.data_dir, environment.exports_dir):
        folder.mkdir()
    os.environ.update(environment.variables())
    try:
        isolation.check_settings(Settings.from_env())
    except isolation.RealResourceError as error:
        _refuse(str(error))
    session.config.stash[_ENVIRONMENT_KEY] = environment
    isolation.activate(guard)


def pytest_sessionfinish(session: pytest.Session) -> None:
    isolation.deactivate()
    environment = session.config.stash.get(_ENVIRONMENT_KEY, None)
    if environment is not None:
        shutil.rmtree(environment.base, ignore_errors=True)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item) -> Generator[None, object, object]:
    guard = isolation.active_guard()
    if guard is not None:
        guard.current_test = item.nodeid
        guard.grants.clear()
    try:
        return (yield)
    finally:
        if guard is not None:
            guard.current_test = None
            guard.grants.clear()


@pytest.fixture
def test_environment(request: pytest.FixtureRequest) -> TestEnvironment:
    """The session's isolated folders, database, Keychain prefix and ports."""
    return request.config.stash[_ENVIRONMENT_KEY]
