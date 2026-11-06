"""T41: software update with rollback, on a fixture project and a fixture tool.

A local package index holds `zzt41lib` 1.0 (good) and 2.0 (its check fails) and
`zzt41tool` 1.0 and 2.0 (the stand-in for Open WebUI, installed as a uv tool). Nothing
reaches the internet: uv runs with the local index only, and the default index points
at a dead local address, so any lookup that ignored that setting fails instead. Marker
`destructive`; it never touches the real project environment or Open WebUI.
"""

import base64
import hashlib
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from company_brain import software_update as su

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
UV = str(Path.home() / ".local" / "bin" / "uv")


def wheel(folder: Path, name: str, version: str, body: str) -> None:
    """A minimal pure-Python wheel, written by hand (no build backend, no network)."""
    dist = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": body,
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        f"{dist}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    if name == "zzt41tool":
        files[f"{dist}/entry_points.txt"] = "[console_scripts]\nzzt41tool = zzt41tool:main\n"
    record = []
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest()).rstrip(b"=")
        record.append(f"{path},sha256={digest.decode()},{len(text.encode())}")
    record.append(f"{dist}/RECORD,,")
    with zipfile.ZipFile(folder / f"{name}-{version}-py3-none-any.whl", "w") as zf:
        for path, text in files.items():
            zf.writestr(path, text)
        zf.writestr(f"{dist}/RECORD", "\n".join(record) + "\n")


@pytest.fixture
def env(tmp_path: Path) -> Iterator[su.Environment]:
    index = tmp_path / "index"
    index.mkdir()
    wheel(index, "zzt41lib", "1.0", "OK = True\n")
    tool = "VERSION = '{v}'\n\ndef main() -> None:\n    print(VERSION)\n"
    wheel(index, "zzt41tool", "1.0", tool.format(v="1.0"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture-project"\nversion = "0"\nrequires-python = ">=3.12"\n'
        'dependencies = ["zzt41lib"]\n'
    )
    uv_env = {
        "UV_DEFAULT_INDEX": "http://127.0.0.1:9/simple",  # dead: an index lookup fails, offline
        "UV_HTTP_RETRIES": "0",
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_TOOL_DIR": str(tmp_path / "tools"),
        "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
        "UV_PYTHON_DOWNLOADS": "never",
    }
    environment = su.Environment(
        project=project,
        tool="zzt41tool",
        uv_env=uv_env,
        check=[
            UV,
            "run",
            "--project",
            str(project),
            "--frozen",
            "python",
            "-c",
            "import zzt41lib, sys; sys.exit(0 if zzt41lib.OK else 1)",
        ],
        state_dir=tmp_path / "state",
        index_args=["--no-index", "--find-links", str(index)],
    )
    run = su.Runner(environment)
    run.uv("lock")
    run.uv("sync", "--frozen")
    run.uv("tool", "install", "zzt41tool==1.0")
    environment.index = index  # type: ignore[attr-defined]
    yield environment


def versions(env: su.Environment) -> tuple[str, str]:
    run = su.Runner(env)
    lib = run.capture(
        UV,
        "run",
        "--project",
        str(env.project),
        "--frozen",
        "python",
        "-c",
        "import importlib.metadata as m; print(m.version('zzt41lib'))",
    )
    tool = run.capture(str(Path(env.uv_env["UV_TOOL_BIN_DIR"]) / "zzt41tool"))
    return lib.strip(), tool.strip()


def publish(env: su.Environment, good: bool) -> None:
    index: Path = env.index  # type: ignore[attr-defined]
    wheel(index, "zzt41lib", "2.0", f"OK = {good}\n")
    tool = "VERSION = '{v}'\n\ndef main() -> None:\n    print(VERSION)\n"
    wheel(index, "zzt41tool", "2.0", tool.format(v="2.0"))


class Probes:
    """Records when the firewall probe ran; `escape` makes one phase report an escape."""

    def __init__(self, escape: str | None = None) -> None:
        self.phases: list[str] = []
        self.escape = escape

    def __call__(self, phase: str) -> list[str]:
        self.phases.append(phase)
        return ["python-ipv4 reached the internet"] if phase == self.escape else []


def test_a_good_update_installs_both(env: su.Environment) -> None:
    publish(env, good=True)
    probes = Probes()
    result = su.update(env, probes=probes, coverage=lambda: [])
    assert result.state == "updated", result
    assert versions(env) == ("2.0", "2.0")
    assert probes.phases == ["before", "during", "after"]  # T41.AC2
    assert not (env.state_dir / su.MARKER).exists()


def test_a_failing_update_rolls_back(env: su.Environment) -> None:
    """T41.AC1: the new version fails the check; everything goes back and the check passes."""
    publish(env, good=False)
    result = su.update(env, probes=Probes(), coverage=lambda: [])
    assert result.state == "rolled back" and "previous versions" in result.message
    assert versions(env) == ("1.0", "1.0")
    assert subprocess.run(env.check, env=su.Runner(env).environ(), check=False).returncode == 0
    assert not (env.state_dir / su.MARKER).exists()


def test_an_escape_or_uncovered_interpreter_rolls_back(env: su.Environment) -> None:
    """T41.AC2: a probe that reaches the internet during or after, or a new interpreter
    path without a firewall rule, rolls the update back."""
    publish(env, good=True)
    for phase in ("during", "after"):
        result = su.update(env, probes=Probes(escape=phase), coverage=lambda: [])
        assert result.state == "rolled back" and "internet" in result.message
        assert versions(env) == ("1.0", "1.0")
    result = su.update(env, probes=Probes(), coverage=lambda: ["New Python is not blocked"])
    assert result.state == "rolled back" and versions(env) == ("1.0", "1.0")
    result = su.update(env, probes=Probes(escape="before"), coverage=lambda: [])
    assert result.state == "not started" and versions(env) == ("1.0", "1.0")


@pytest.mark.parametrize("step", su.STEPS)
def test_an_interrupted_update_is_finished_or_rolled_back(env: su.Environment, step: str) -> None:
    """T41.AC3: the update dies after each step; the next start completes or rolls back."""
    publish(env, good=True)

    class Died(BaseException):
        pass

    def die(at: str) -> None:
        if at == step:
            raise Died

    with pytest.raises(Died):
        su.update(env, probes=Probes(), coverage=lambda: [], after_step=die)
    assert (env.state_dir / su.MARKER).exists()
    message = su.recover(env, probes=Probes(), coverage=lambda: [])
    assert message
    assert not (env.state_dir / su.MARKER).exists()
    expected = ("2.0", "2.0") if step == su.STEPS[-1] else ("1.0", "1.0")
    assert versions(env) == expected
    assert subprocess.run(env.check, env=su.Runner(env).environ(), check=False).returncode == 0


def test_start_ai_finishes_or_refuses_after_an_interrupted_update(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """T41.AC3 at the launcher: Start AI runs the recovery first and refuses to start when
    an interrupted update could not be undone."""
    from company_brain import launcher

    started: list[bool] = []
    monkeypatch.setattr(
        su,
        "recover",
        lambda *_a: (
            "An interrupted software update could not be undone. Do not start the AI; ask for help."
        ),
    )
    monkeypatch.setattr(launcher, "start", lambda *_a, **_k: started.append(True))
    assert launcher.main(["start"]) == 1
    assert not started and "could not be undone" in capsys.readouterr().out
