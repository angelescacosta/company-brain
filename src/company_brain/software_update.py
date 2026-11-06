"""Update the software, with rollback (T41): Python packages and Open WebUI only.

Models stay pinned. `uv` itself is not blocked by the firewall, so it downloads
and verifies the new versions (into its cache, through staging environments)
while the AI's own programs stay blocked; installing then works offline from
that cache. After installing, the firewall's coverage of any new interpreter
path is checked again (T4.AC4), then CHK5 runs. Any failure rolls back to the
previous lock file and Open WebUI version. A marker file records the last
completed step, so the next Start AI finishes (checks passed) or rolls back an
interrupted update before starting. Firewall probes run before, during and
after the update; an escape stops or rolls back.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

UV = str(Path.home() / ".local" / "bin" / "uv")
MARKER = "update_in_progress.json"
STEPS = ("snapshot", "download", "install", "check")

Probe = Callable[[str], list[str]]  # phase -> problems (a blocked program got out)
Coverage = Callable[[], list[str]]  # firewall rules missing for an interpreter path


@dataclass
class Environment:
    project: Path
    tool: str
    check: list[str]
    state_dir: Path
    uv_env: dict[str, str] = field(default_factory=dict)
    index_args: list[str] = field(default_factory=list)  # e.g. a local index for tests


@dataclass(frozen=True)
class Result:
    state: str  # updated, rolled back, not started, failed
    message: str


class Runner:
    def __init__(self, env: Environment) -> None:
        self.env = env

    def environ(self) -> dict[str, str]:
        base = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_OFFLINE")}
        return {**base, **self.env.uv_env}

    def run(
        self, *args: str, extra: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed programs, arguments built here
            list(args),
            cwd=self.env.project,
            env={**self.environ(), **(extra or {})},
            capture_output=True,
            text=True,
            check=True,
        )

    def uv(self, *args: str, extra: dict[str, str] | None = None) -> str:
        resolves = args[:1] in (("lock",), ("sync",)) or args[:2] in (
            ("pip", "install"),
            ("tool", "install"),
        )
        index = self.env.index_args if resolves else []
        return self.run(UV, *args, *index, extra=extra).stdout

    def capture(self, *args: str) -> str:
        return self.run(*args).stdout


def tool_version(run: Runner, tool: str) -> str | None:
    for line in run.uv("tool", "list").splitlines():
        match = re.match(rf"^{re.escape(tool)} v(\S+)", line)
        if match:
            return match.group(1)
    return None


def _marker(env: Environment) -> Path:
    return env.state_dir / MARKER


def _write_marker(env: Environment, **values: object) -> None:
    path = _marker(env)
    current = json.loads(path.read_text()) if path.exists() else {}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({**current, **values}), encoding="utf-8")
    temporary.replace(path)


def _clear(env: Environment) -> None:
    for name in ("previous", "staging-venv", "staging-tool"):
        shutil.rmtree(env.state_dir / name, ignore_errors=True)
    _marker(env).unlink(missing_ok=True)


def _check_passes(env: Environment, run: Runner) -> bool:
    done = subprocess.run(  # noqa: S603 - the fixed check command
        env.check, cwd=env.project, env=run.environ(), capture_output=True, check=False
    )
    return done.returncode == 0


def rollback(env: Environment) -> bool:
    """Back to the snapshot (lock file and tool version); True when the check passes again."""
    run = Runner(env)
    marker = json.loads(_marker(env).read_text()) if _marker(env).exists() else {}
    saved = env.state_dir / "previous" / "uv.lock"
    if saved.exists():
        shutil.copyfile(saved, env.project / "uv.lock")
    for offline in (True, False):  # the cache normally holds the previous versions
        try:
            run.uv("sync", "--frozen", *(["--offline"] if offline else []))
            old = marker.get("tool_version")
            if old and tool_version(run, env.tool) != old:
                run.uv(
                    "tool",
                    "install",
                    "--force",
                    f"{env.tool}=={old}",
                    *(["--offline"] if offline else []),
                )
            break
        except subprocess.CalledProcessError:
            if not offline:
                return False
    ok = _check_passes(env, run)
    if ok:
        _clear(env)
    return ok


def update(
    env: Environment,
    probes: Probe,
    coverage: Coverage,
    after_step: Callable[[str], None] | None = None,
) -> Result:
    after_step = after_step or (lambda _step: None)
    run = Runner(env)
    escaped = probes("before")
    if escaped:
        return Result("not started", "Not updating: " + "; ".join(escaped) + ".")
    env.state_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now().isoformat(timespec="seconds")
    _write_marker(env, step="started", started=started, tool_version=None)
    # Snapshot.
    previous = env.state_dir / "previous"
    shutil.rmtree(previous, ignore_errors=True)
    previous.mkdir()
    shutil.copyfile(env.project / "uv.lock", previous / "uv.lock")
    _write_marker(env, step="snapshot", tool_version=tool_version(run, env.tool))
    after_step("snapshot")
    failure = ""
    try:
        # Download and verify into uv's cache, without touching the live environments.
        run.uv("lock", "--upgrade")
        staging = env.state_dir / "staging-venv"
        run.uv("sync", "--frozen", extra={"UV_PROJECT_ENVIRONMENT": str(staging)})
        tool_stage = env.state_dir / "staging-tool"
        run.uv("venv", "--quiet", str(tool_stage))
        run.uv("pip", "install", "--python", str(tool_stage), "--upgrade", env.tool)
        target = _installed(run, tool_stage, env.tool)
        _write_marker(env, step="download", tool_target=target)
        after_step("download")
        escaped = probes("during")
        if escaped:
            failure = "a blocked program reached the internet: " + "; ".join(escaped)
            raise RuntimeError(failure)
        # Install, offline, from the verified downloads.
        run.uv("sync", "--frozen", "--offline")
        run.uv("tool", "install", "--force", f"{env.tool}=={target}", "--offline")
        _write_marker(env, step="install")
        after_step("install")
        problems = coverage()
        if problems:
            failure = "the firewall does not cover a new program: " + "; ".join(problems)
            raise RuntimeError(failure)
        if not _check_passes(env, run):
            failure = "the checks failed with the new versions"
            raise RuntimeError(failure)
        escaped = probes("after")
        if escaped:
            failure = "a blocked program reached the internet: " + "; ".join(escaped)
            raise RuntimeError(failure)
        _write_marker(env, step="check")
        after_step("check")
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        reason = failure or f"a step failed ({type(error).__name__})"
        if isinstance(error, subprocess.CalledProcessError):
            detail = (error.stderr or "").strip().splitlines()[-1:] or [""]
            reason += f": {' '.join(str(a) for a in error.cmd[1:3])}: {detail[0][:200]}"
        if rollback(env):
            return Result(
                "rolled back",
                f"The update was undone ({reason}); the previous "
                "versions are back and the checks pass.",
            )
        return Result(
            "failed",
            f"The update failed ({reason}) and could not be fully undone. "
            "Do not start the AI; ask for help.",
        )
    _clear(env)
    return Result("updated", "The software was updated and the checks pass.")


def _installed(run: Runner, venv: Path, name: str) -> str:
    """The version of `name` installed in a staging environment."""
    for line in run.uv("pip", "list", "--python", str(venv), "--format", "freeze").splitlines():
        package, _, version = line.partition("==")
        if package.lower().replace("_", "-") == name.lower().replace("_", "-"):
            return version
    raise RuntimeError(f"{name} was not installed in the staging environment")


def recover(env: Environment, probes: Probe, coverage: Coverage) -> str:
    """At Start AI: finish an update whose checks passed, or roll back an interrupted one."""
    marker = _marker(env)
    if not marker.exists():
        return ""
    step = json.loads(marker.read_text()).get("step")
    if step == STEPS[-1]:
        _clear(env)
        return "An interrupted software update had passed its checks; it was completed."
    if not (env.state_dir / "previous" / "uv.lock").exists():
        _clear(env)  # it stopped before changing anything
        return "An interrupted software update had not changed anything; it was cleared."
    if rollback(env):
        return "An interrupted software update was undone; the previous versions are back."
    return "An interrupted software update could not be undone. Do not start the AI; ask for help."
