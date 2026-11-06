"""Start AI, Stop AI and Check health (T6).

The services run as per-user launchd jobs (no administrator rights). Their job
files live in the data folder, not in ~/Library/LaunchAgents, so nothing starts
by itself at login: the owner starts the AI with the Start AI launcher, which
checks the firewall every time (so a reboot cannot skip the check).

Order on start (D29): LuLu's state is checked first. Online, PostgreSQL and
Ollama start next, because the Ollama probe needs the Ollama server, then the
probes run; any probe that gets out, or an inconclusive result, stops
everything again and Start refuses. The assistant (T29) and Open WebUI start last.
"""

from __future__ import annotations

import os
import plistlib
import signal
import socket
import subprocess
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from company_brain import egress, isolation
from company_brain.firewall import ProbeResult, listeners, probe_outcome, verdict
from company_brain.preflight import THRESHOLD_BYTES, free_bytes_on
from company_brain.privacy_config import parse, problems
from company_brain.settings import Settings

REPO = Path(__file__).resolve().parents[2]
PG_BIN = Path(egress.POSTGRES).parent
OPEN_WEBUI = Path.home() / ".local" / "bin" / "open-webui"
OPEN_WEBUI_PYTHON = Path.home() / ".local" / "share" / "uv" / "tools" / "open-webui" / "bin"
START_TIMEOUT = 60.0


@dataclass(frozen=True)
class Service:
    name: str
    title: str
    label: str
    program: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    port: int
    command_prefix: str


def define(settings: Settings) -> list[Service]:
    """The four services, in start order: database, model server, assistant, chat window."""
    home = {"HOME": str(Path.home()), "LANG": "en_US.UTF-8"}
    prefix = settings.launchd_prefix
    env_file = (REPO / "config" / "openwebui.env").read_text(encoding="utf-8")
    webui_dir = settings.data_dir / "openwebui"
    webui_env = {
        **parse(env_file)[0],
        **home,
        "PORT": str(settings.openwebui_port),
        "CORS_ALLOW_ORIGIN": f"http://127.0.0.1:{settings.openwebui_port}",
        "DATA_DIR": str(webui_dir),
    }
    return [
        Service(
            name="postgresql",
            title="The database (PostgreSQL)",
            label=f"{prefix}.postgresql",
            program=(
                egress.POSTGRES,
                "-D",
                str(settings.pg_data),
                "-p",
                str(settings.pg_port),
                "-c",
                "listen_addresses=127.0.0.1",
            ),
            env=home,
            cwd=settings.pg_data,
            port=settings.pg_port,
            command_prefix="postgres",
        ),
        Service(
            name="ollama",
            title="The AI model server (Ollama)",
            label=f"{prefix}.ollama",
            program=(egress.OLLAMA, "serve"),
            env={**home, "OLLAMA_HOST": f"127.0.0.1:{settings.ollama_port}"},
            cwd=settings.data_dir,
            port=settings.ollama_port,
            command_prefix="ollama",
        ),
        Service(
            name="backend",
            title="The assistant (Company Brain)",
            label=f"{prefix}.backend",
            program=(str(REPO / ".venv" / "bin" / "python"), "-m", "company_brain.backend"),
            env=backend_env(settings),
            cwd=REPO,
            port=settings.backend_port,
            command_prefix="python3",
        ),
        Service(
            name="openwebui",
            title="The chat window (Open WebUI)",
            label=f"{prefix}.openwebui",
            program=(
                str(OPEN_WEBUI),
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(settings.openwebui_port),
            ),
            env=webui_env,
            cwd=webui_dir,
            port=settings.openwebui_port,
            command_prefix="python3",
        ),
    ]


def backend_env(settings: Settings) -> dict[str, str]:
    """The assistant reads its settings (and the test copies' ports) from its environment."""
    env = {
        "HOME": str(Path.home()),
        "LANG": "en_US.UTF-8",
        isolation.ENV_DB: settings.database,
        isolation.ENV_KEYCHAIN: settings.keychain_prefix,
        isolation.ENV_DATA: str(settings.data_dir),
        isolation.ENV_EXPORTS: str(settings.exports_dir),
        isolation.ENV_BACKEND_PORT: str(settings.backend_port),
        isolation.ENV_OPENWEBUI_PORT: str(settings.openwebui_port),
        isolation.ENV_OLLAMA_PORT: str(settings.ollama_port),
        isolation.ENV_PG_PORT: str(settings.pg_port),
        isolation.ENV_PG_DATA: str(settings.pg_data),
        isolation.ENV_LAUNCHD: settings.launchd_prefix,
        "COMPANY_BRAIN_OLLAMA_URL": f"http://127.0.0.1:{settings.ollama_port}",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TIKTOKEN_CACHE_DIR": str(Path.home() / ".cache" / "tiktoken"),
    }
    if settings.root is not None:
        env[isolation.ENV_ROOT] = str(settings.root)
    return env


def job(service: Service) -> dict[str, object]:
    """The launchd job. Output goes nowhere: service logs could hold content (T8)."""
    return {
        "Label": service.label,
        "ProgramArguments": list(service.program),
        "EnvironmentVariables": dict(service.env),
        "WorkingDirectory": str(service.cwd),
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Interactive",
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }


class Launchd:
    """Per-user launchd jobs through launchctl."""

    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir
        self.domain = f"gui/{os.getuid()}"

    def _launchctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed program, arguments built here
            ["/bin/launchctl", *args], capture_output=True, text=True, check=False
        )

    def loaded(self, label: str) -> bool:
        return self._launchctl("print", f"{self.domain}/{label}").returncode == 0

    def _bootout(self, label: str) -> None:
        # launchd removes the job a moment after bootout returns; bootstrapping it again
        # before then fails with "Bootstrap failed: 5".
        self._launchctl("bootout", f"{self.domain}/{label}")
        deadline = time.monotonic() + 10
        while self.loaded(label) and time.monotonic() < deadline:
            time.sleep(0.1)

    def start(self, service: Service) -> None:
        if self.loaded(service.label):
            self._bootout(service.label)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        path = self.jobs_dir / f"{service.label}.plist"
        path.write_bytes(plistlib.dumps(job(service)))
        result = self._launchctl("bootstrap", self.domain, str(path))
        if result.returncode != 0:
            raise RuntimeError(f"launchctl could not start {service.name}: {result.stderr.strip()}")

    def install(self, plist: dict[str, object]) -> None:
        """Load a scheduled job (for example the nightly update) until the next logout."""
        label = str(plist["Label"])
        if self.loaded(label):
            self._bootout(label)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        path = self.jobs_dir / f"{label}.plist"
        path.write_bytes(plistlib.dumps(plist))
        self._launchctl("bootstrap", self.domain, str(path))

    def stop(self, label: str) -> None:
        if self.loaded(label):
            self._bootout(label)


def listening(port: int) -> list[tuple[str, int, str, int]]:
    output = subprocess.run(  # noqa: S603 - fixed program, arguments built here
        ["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return [entry for entry in listeners(output) if entry[3] == port]


def uid_of(pid: int) -> int | None:
    output = subprocess.run(  # noqa: S603 - fixed program, arguments built here
        ["/bin/ps", "-o", "uid=", "-p", str(pid)], capture_output=True, text=True, check=False
    ).stdout.strip()
    return int(output) if output.isdigit() else None


def owned(service: Service) -> bool:
    """The program listening on the port is this service's own, run by the owner. Another
    account can bind a loopback port first; its process is not even visible here (F4)."""
    return any(
        command.startswith(service.command_prefix) and uid_of(pid) == os.getuid()
        for command, pid, _address, _port in listening(service.port)
    )


def _answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def ready(service: Service) -> bool:
    if not _answers(service.port) or not owned(service):
        return False
    if service.name == "openwebui":
        return _http_ok(service.port)
    return True


def _http_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:  # noqa: S310 - loopback only
            return bool(response.status == 200)
    except OSError:
        return False


@dataclass
class Gate:
    """The firewall check: LuLu's state, whether the internet is reachable, the probes."""

    static_problems: Callable[[], list[str]]
    online: Callable[[], bool]
    probes: Callable[[str], list[ProbeResult]]


def real_gate() -> Gate:
    executables = {
        "Python (AI services)": egress.PYTHON,
        "Open WebUI's Python": os.path.realpath(OPEN_WEBUI_PYTHON / "python"),
        "Ollama": egress.OLLAMA,
        "PostgreSQL": egress.POSTGRES,
    }
    return Gate(
        static_problems=lambda: egress.lulu_problems(executables),
        online=egress.internet_reachable,
        probes=egress.run_probes,
    )


@dataclass
class Outcome:
    ok: bool
    messages: list[str] = field(default_factory=list)


def _wait(services: Sequence[Service], deadline: float) -> list[Service]:
    waiting = list(services)
    while waiting and time.monotonic() < deadline:
        waiting = [s for s in waiting if not ready(s)]
        if waiting:
            time.sleep(0.5)
    return waiting


def _probe_message(probes: Sequence[ProbeResult]) -> str:
    result = verdict(probes)
    escaped = [p.name for p in probes if probe_outcome(p) == "fail"]
    if escaped:
        return f"A blocked program reached the internet ({', '.join(escaped)})."
    unsure = [p.name for p in probes if probe_outcome(p) == "inconclusive"]
    if result == "inconclusive" and unsure:
        return f"The firewall test could not confirm the block ({', '.join(unsure)})."
    return "The firewall test could not run against the internet."


def start(
    settings: Settings,
    gate: Gate,
    launchd: Launchd,
    open_browser: Callable[[str], None] | None = None,
    timeout: float = START_TIMEOUT,
) -> Outcome:
    began = time.monotonic()
    deadline = began + timeout
    env_problems = problems((REPO / "config" / "openwebui.env").read_text(encoding="utf-8"))
    if env_problems:
        return Outcome(False, ["Open WebUI's privacy settings are wrong:", *env_problems])
    lulu = gate.static_problems()
    if lulu:
        return Outcome(False, ["Not starting: the firewall is not set up as it should be.", *lulu])
    online = gate.online()
    services = define(settings)
    taken = [s for s in services if _answers(s.port) and not owned(s)]
    if taken:  # never take another program for ours, nor send it a password or token (F4)
        return Outcome(
            False,
            [
                f"Not starting: another program is using the address of {s.title} "
                f"(port {s.port}). Restart the Mac, then click Start AI again."
                for s in taken
            ],
        )
    make_private(settings.data_dir)
    first, rest = services[:2], services[2:]  # the probes need Ollama; the rest start after
    for service in first:
        launchd.start(service)
    late = _wait(first, deadline)
    if late:
        stop(settings, launchd)
        return Outcome(False, [f"{s.title} did not start in time." for s in late])
    if online:
        probes = gate.probes(f"127.0.0.1:{settings.ollama_port}")
        if verdict(probes) != "pass":
            stop(settings, launchd)
            return Outcome(False, ["Not starting: " + _probe_message(probes)])
    for service in rest:
        launchd.start(service)
    late = _wait(rest, deadline)
    if late:
        stop(settings, launchd)
        return Outcome(False, [f"{s.title} did not start in time." for s in late])
    url = f"http://127.0.0.1:{settings.openwebui_port}"
    if open_browser is not None:
        open_browser(url)
    seconds = time.monotonic() - began
    mode = "online, firewall test passed" if online else "offline, firewall rules checked"
    return Outcome(True, [f"The AI is running ({mode}) after {seconds:.0f} seconds: {url}"])


def _stop_postgres(settings: Settings) -> None:
    if (settings.pg_data / "postmaster.pid").exists():
        subprocess.run(  # noqa: S603 - fixed program, arguments built here
            [str(PG_BIN / "pg_ctl"), "stop", "-D", str(settings.pg_data), "-m", "fast", "-w"],
            capture_output=True,
            check=False,
            timeout=60,
        )


def stop(settings: Settings, launchd: Launchd, timeout: float = 30.0) -> Outcome:
    """Stop every service, including copies started by hand; harmless when already stopped."""
    services = define(settings)
    for service in reversed(services):
        if service.name == "postgresql":
            _stop_postgres(settings)
        launchd.stop(service.label)
    deadline = time.monotonic() + timeout
    for service in services:
        for command, pid, _address, _port in listening(service.port):
            if command.startswith(service.command_prefix):
                os.kill(pid, signal.SIGTERM)
    left = [s for s in services if listening(s.port)]
    while left and time.monotonic() < deadline:
        time.sleep(0.5)
        left = [s for s in services if listening(s.port)]
    if left:
        return Outcome(False, [f"{s.title} is still running." for s in left])
    return Outcome(True, ["The AI is stopped."])


def health(
    settings: Settings,
    gate: Gate,
    last_update: Callable[[], datetime | None],
    free_bytes: Callable[[Path], int] = free_bytes_on,
) -> Outcome:
    messages = []
    ok = True
    running = set()
    for service in define(settings):
        found = listening(service.port)
        if found:
            running.add(service.name)
        exposed = [address for _c, _p, address, _port in found if address != "127.0.0.1"]
        if not found:
            ok = False
            messages.append(f"Problem: {service.title} is not running. Click Start AI.")
        elif exposed:
            ok = False
            messages.append(f"Problem: {service.title} can be reached from other computers.")
        else:
            messages.append(f"OK: {service.title} is running.")
    lulu = gate.static_problems()
    if lulu:
        ok = False
        messages.append("Problem: the firewall is not set up as it should be.")
        messages += [f"  {line}" for line in lulu]
    elif "ollama" not in running:
        # The live probe needs the model server; the rules were checked above.
        messages.append("OK: the firewall rules are in place (the AI is stopped, so no live test).")
    elif gate.online():
        probes = gate.probes(f"127.0.0.1:{settings.ollama_port}")
        if verdict(probes) == "pass":
            messages.append("OK: the firewall blocks the AI from the internet (tested now).")
        else:
            ok = False
            messages.append("Problem: " + _probe_message(probes))
    else:
        messages.append("OK: the firewall rules are in place (no internet, so no live test).")
    free = free_bytes(settings.data_dir if settings.data_dir.exists() else Path.home())
    gigabytes = free / 1_000_000_000
    if free < THRESHOLD_BYTES:
        ok = False
        messages.append(f"Problem: only {gigabytes:.0f} GB free on the disk; 15 GB are needed.")
    else:
        messages.append(f"OK: {gigabytes:.0f} GB free on the disk.")
    try:
        when = last_update()
    except Exception:  # noqa: BLE001 - any database failure is reported in plain English
        ok = False
        messages.append("Problem: the last index update could not be read (is the database up?).")
    else:
        if when is None:
            messages.append("The documents have not been indexed yet.")
        else:
            messages.append(f"Last index update: {when:%d %B %Y, %H:%M}.")
    return Outcome(ok, messages)


def make_private(data_dir: Path) -> None:
    """The data folder and Open WebUI's database, which holds the Pipe's token, are the
    owner's only: another account on the Mac cannot read them (security scan F3)."""
    webui = data_dir / "openwebui"
    webui.mkdir(mode=0o700, parents=True, exist_ok=True)
    for folder in (data_dir, webui):
        folder.chmod(0o700)
    database = webui / "webui.db"
    if database.exists():
        database.chmod(0o600)


def jobs_dir(settings: Settings) -> Path:
    return settings.data_dir / "launchd"
