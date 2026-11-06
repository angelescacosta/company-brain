"""T1.AC3: tests of every marker are stopped from using real resources.

Each case runs an inner pytest session in a subprocess (pytester), so the
inner tests can carry the `slow`, `destructive` and `release` markers that
CHK5 deselects, and so the inner guard is independent of this session's.
"""

from pathlib import Path

import pytest

MARKERS = ["", "integration", "slow", "destructive", "release"]

INI = """
[pytest]
addopts = -p company_brain.testing.pytest_plugin --strict-markers
markers =
    integration: x
    slow: x
    destructive: x
    release: x
"""

ATTEMPTS = """
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from company_brain.isolation import RealResourceError, real_resources
from company_brain.settings import Settings

REAL_DATA = Path.home() / "CompanyBrainData"
DRIVE = Path.home() / "Library" / "CloudStorage"
MARK


@MARK
def test_open_real_data():
    with pytest.raises(RealResourceError):
        open(REAL_DATA / "probe.txt").close()


@MARK
def test_write_real_exports():
    with pytest.raises(RealResourceError):
        (Path.home() / "CompanyBrainExports" / "probe.txt").write_text("x")


@MARK
def test_list_drive():
    with pytest.raises(RealResourceError):
        os.listdir(DRIVE)


@MARK
def test_settings_real_database(monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_DB", "company_brain")
    with pytest.raises(RealResourceError):
        Settings.from_env()


@MARK
def test_settings_real_data_dir(monkeypatch):
    monkeypatch.delenv("COMPANY_BRAIN_DATA")
    with pytest.raises(RealResourceError):
        Settings.from_env()


@MARK
def test_settings_real_keychain(monkeypatch):
    monkeypatch.setenv("COMPANY_BRAIN_KEYCHAIN_PREFIX", "company-brain")
    with pytest.raises(RealResourceError):
        Settings.from_env()


@MARK
def test_settings_real_service_folder_and_ports(monkeypatch):
    for key, value in (
        ("COMPANY_BRAIN_PG_DATA", str(Path.home() / "Library/Application Support/Postgres/var-18")),
        ("COMPANY_BRAIN_PG_PORT", "5432"),
        ("COMPANY_BRAIN_OLLAMA_PORT", "11434"),
        ("COMPANY_BRAIN_LAUNCHD_PREFIX", "com.companybrain"),
    ):
        with monkeypatch.context() as patch:
            patch.setenv(key, value)
            with pytest.raises(RealResourceError):
                Settings.from_env()


@MARK
def test_subprocess_real_launchd_label():
    with pytest.raises(RealResourceError):
        subprocess.run(["launchctl", "print", "gui/501/com.companybrain.ollama"], check=False)


@MARK
def test_real_service_port():
    with pytest.raises(RealResourceError):
        socket.create_connection(("127.0.0.1", 8080), timeout=0.2)


@MARK
def test_subprocess_argument():
    with pytest.raises(RealResourceError):
        subprocess.run(["cat", str(REAL_DATA / "probe.txt")], check=False)


@MARK
def test_subprocess_environment():
    env = {**os.environ, "COMPANY_BRAIN_DATA": str(REAL_DATA)}
    with pytest.raises(RealResourceError):
        subprocess.run(["true"], env=env, check=False)


@MARK
def test_subprocess_database():
    with pytest.raises(RealResourceError):
        subprocess.run(["psql", "-d", "company_brain"], check=False)


@MARK
def test_subprocess_keychain():
    with pytest.raises(RealResourceError):
        subprocess.run(
            ["security", "find-generic-password", "-s", "company-brain-db"], check=False
        )


@MARK
def test_shell_command():
    with pytest.raises(RealResourceError):
        os.system("ls ~/CompanyBrainData")


@MARK
def test_child_process_guards_itself():
    code = (
        "import os, sys\\n"
        "os.environ.pop('COMPANY_BRAIN_DATA')\\n"
        "from company_brain.settings import Settings\\n"
        "Settings.from_env()\\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode != 0
    assert "RealResourceError" in result.stderr


@MARK
def test_child_process_file_open_guarded():
    code = (
        "import company_brain, pathlib\\n"
        "parts = ['Company', 'BrainData']\\n"
        "open(pathlib.Path.home() / ''.join(parts) / 'probe.txt')\\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode != 0
    assert "RealResourceError" in result.stderr


@MARK
def test_marker_alone_grants_nothing():
    with pytest.raises(RealResourceError), real_resources("data-dir"):
        pass


@MARK
def test_test_resources_allowed(test_environment):
    probe = test_environment.data_dir / "probe.txt"
    probe.write_text("ok")
    assert probe.read_text() == "ok"
    assert subprocess.run(["cat", str(probe)], capture_output=True).returncode == 0
"""


def _write_attempts(pytester: pytest.Pytester, marker: str) -> None:
    pytester.makeini(INI)
    mark = f"pytest.mark.{marker}" if marker else "pytest.mark.usefixtures()"
    source = ATTEMPTS.replace("@MARK", f"@{mark}").replace("\nMARK\n", "\n")
    pytester.makepyfile(test_attempts=source)


@pytest.mark.parametrize("marker", MARKERS, ids=[m or "unmarked" for m in MARKERS])
def test_real_resources_stopped_for_each_marker(pytester: pytest.Pytester, marker: str) -> None:
    _write_attempts(pytester, marker)
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=18)


ALLOWLISTED = """
from pathlib import Path

import pytest

from company_brain.isolation import RealResourceError, real_resources

REAL_DATA = Path.home() / "CompanyBrainData"


@pytest.mark.release
def test_listed_read(tmp_path):
    with real_resources("data-dir"):
        with pytest.raises(FileNotFoundError):
            open(REAL_DATA / "does-not-exist-probe.txt").close()


@pytest.mark.release
def test_listed_but_write_not_granted():
    with pytest.raises(RealResourceError):
        with real_resources("data-dir", writes=["data-dir:anything"]):
            pass


@pytest.mark.release
def test_listed_but_resource_not_granted():
    with pytest.raises(RealResourceError):
        with real_resources("system-db"):
            pass


@pytest.mark.release
def test_listed_read_grant_does_not_allow_write():
    with real_resources("data-dir"):
        with pytest.raises(RealResourceError):
            open(REAL_DATA / "probe.txt", "w").close()


@pytest.mark.release
def test_grant_ends_with_block():
    with real_resources("data-dir"):
        pass
    with pytest.raises(RealResourceError):
        open(REAL_DATA / "probe.txt").close()
"""

ALLOWLIST = """
[[exception]]
test = "test_listed.py::*"
task = "T0"
resources = ["data-dir"]
writes = []
reason = "Fixture for the guard's own tests."
"""


def test_allowlist_grants_only_listed_resources(pytester: pytest.Pytester) -> None:
    pytester.makeini(INI)
    (pytester.path / "config").mkdir()
    (pytester.path / "config" / "real_resource_allowlist.toml").write_text(ALLOWLIST)
    pytester.makepyfile(test_listed=ALLOWLISTED)
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=5)


def test_session_refuses_real_environment(pytester: pytest.Pytester) -> None:
    pytester.makeini(INI)
    pytester.makeconftest(
        "import os, pathlib\n"
        "os.environ['COMPANY_BRAIN_DATA'] = str(pathlib.Path.home() / 'CompanyBrainData')\n"
    )
    pytester.makepyfile(test_nothing="def test_nothing():\n    pass\n")
    result = pytester.runpytest_subprocess("-q")
    assert result.ret == 3
    result.stdout.fnmatch_lines(["*Test environment refused*COMPANY_BRAIN_DATA*"])


def test_session_refuses_real_database(pytester: pytest.Pytester) -> None:
    pytester.makeini(INI)
    pytester.makeconftest("import os\nos.environ['PGDATABASE'] = 'company_brain'\n")
    pytester.makepyfile(test_nothing="def test_nothing():\n    pass\n")
    result = pytester.runpytest_subprocess("-q")
    assert result.ret == 3


def test_this_session_uses_test_resources(test_environment: object) -> None:
    from company_brain.settings import Settings

    settings = Settings.from_env()
    assert settings.database == "company_brain_test"
    assert settings.keychain_prefix.startswith("company-brain-test")
    assert not str(settings.data_dir).startswith(str(Path.home() / "CompanyBrainData"))
