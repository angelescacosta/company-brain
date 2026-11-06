"""T2.AC1 and T2.AC2: installed tools and libraries match `config/versions.toml`."""

import importlib.metadata
import os
import plistlib
import re
import shutil
import subprocess
import time
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
VERSIONS = tomllib.loads((REPO / "config" / "versions.toml").read_text())
TOOLS = VERSIONS["tools"]
PG_BIN = Path(TOOLS["postgres_app"]["bin_dir"])
TEMP_PG_PORT = 15433


def _output(*command: str) -> str:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    return result.stdout + result.stderr


def _bundle_version(app: str) -> str:
    with open(Path(app) / "Contents" / "Info.plist", "rb") as handle:
        return str(plistlib.load(handle)["CFBundleShortVersionString"])


def test_ollama() -> None:
    binary = str(Path(TOOLS["ollama"]["installed_path"]).expanduser())
    assert TOOLS["ollama"]["version"] in _output(binary, "--version")


def test_age() -> None:
    binary = str(Path(TOOLS["age"]["installed_path"]).expanduser())
    assert _output(binary, "--version").strip() == "v" + TOOLS["age"]["version"]


def test_postgres_app() -> None:
    assert (
        _bundle_version(TOOLS["postgres_app"]["installed_path"]) == TOOLS["postgres_app"]["version"]
    )
    out = _output(str(PG_BIN / "postgres"), "--version")
    assert f"PostgreSQL) {TOOLS['postgres_app']['postgresql']} " in out


@pytest.mark.parametrize("name", ["lulu", "google_drive"])
def test_app_bundles(name: str) -> None:
    assert _bundle_version(TOOLS[name]["installed_path"]) == TOOLS[name]["version"]


def test_open_webui() -> None:
    out = _output("uv", "tool", "list")
    assert re.search(rf"^open-webui v{re.escape(TOOLS['open_webui']['version'])}$", out, re.M)


def test_playwright_chromium() -> None:
    cache = Path.home() / "Library" / "Caches" / "ms-playwright"
    assert (cache / f"chromium-{TOOLS['playwright_chromium']['revision']}").is_dir()


@pytest.mark.parametrize("name", sorted(VERSIONS["libraries"]))
def test_python_libraries(name: str) -> None:
    assert importlib.metadata.version(name) == VERSIONS["libraries"][name]


@pytest.fixture
def temporary_cluster(tmp_path: Path) -> Iterator[Path]:
    """A throwaway PostgreSQL cluster on a test port, never the real database."""
    data = tmp_path / "pg"
    env = {**os.environ, "LC_ALL": "C"}
    subprocess.run(
        [str(PG_BIN / "initdb"), "-D", str(data), "-U", "postgres", "-A", "trust"],
        check=True,
        capture_output=True,
        env=env,
    )
    socket_dir = Path("/tmp") / f"cbpg{os.getpid()}"  # Unix sockets need a short path.
    socket_dir.mkdir(exist_ok=True)
    options = f"-p {TEMP_PG_PORT} -c listen_addresses='' -k {socket_dir}"
    subprocess.run(
        [str(PG_BIN / "pg_ctl"), "-D", str(data), "-l", str(tmp_path / "pg.log")]
        + ["-o", options, "-w", "start"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        yield socket_dir
    finally:
        subprocess.run(
            [str(PG_BIN / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        shutil.rmtree(socket_dir, ignore_errors=True)


def test_pgvector_extension(temporary_cluster: Path) -> None:
    sql = "CREATE EXTENSION vector; SELECT extversion FROM pg_extension WHERE extname = 'vector';"
    for _ in range(20):
        result = subprocess.run(
            [
                str(PG_BIN / "psql"),
                "-h",
                str(temporary_cluster),
                "-p",
                str(TEMP_PG_PORT),
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-tA",
                "-c",
                sql,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            break
        time.sleep(0.25)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == TOOLS["pgvector"]["version"]
