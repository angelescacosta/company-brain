"""Cooperative guard that keeps tests away from real resources (T1).

While the test suite runs, an audit hook refuses file access under the real
data, export and Drive folders, connections to the real service ports, and
subprocesses whose arguments or environment name a real resource. Tests listed
in `config/real_resource_allowlist.toml` may use named real resources inside a
`real_resources()` block. This guard is part of our own code: it is tested, but
it does not stop a program that deliberately bypasses it.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import threading
import tomllib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from company_brain.settings import Settings

ENV_TEST_FLAG = "COMPANY_BRAIN_TEST_ENV"
ENV_GRANT = "COMPANY_BRAIN_TEST_GRANT"
ENV_ROOT = "COMPANY_BRAIN_ROOT"
ENV_DATA = "COMPANY_BRAIN_DATA"
ENV_EXPORTS = "COMPANY_BRAIN_EXPORTS"
ENV_DB = "COMPANY_BRAIN_DB"
ENV_KEYCHAIN = "COMPANY_BRAIN_KEYCHAIN_PREFIX"
ENV_OPENWEBUI_PORT = "COMPANY_BRAIN_OPENWEBUI_PORT"
ENV_BACKEND_PORT = "COMPANY_BRAIN_BACKEND_PORT"
ENV_OLLAMA_PORT = "COMPANY_BRAIN_OLLAMA_PORT"
ENV_PG_PORT = "COMPANY_BRAIN_PG_PORT"
ENV_PG_DATA = "COMPANY_BRAIN_PG_DATA"
ENV_LAUNCHD = "COMPANY_BRAIN_LAUNCHD_PREFIX"

REAL_DATABASE = "company_brain"
TEST_DATABASE = "company_brain_test"
REAL_KEYCHAIN_PREFIX = "company-brain"
TEST_KEYCHAIN_PREFIX = "company-brain-test"
REAL_PORTS = {"openwebui": 8080, "backend": 8765}
TEST_PORTS = {"openwebui": 18080, "backend": 18765}
# Tests share the real Ollama and PostgreSQL read-only (test database), so
# these ports are not refused on connect; the launcher may not manage them.
REAL_SERVICE_PORTS = {"ollama": 11434, "postgresql": 5432}
TEST_SERVICE_PORTS = {"ollama": 21434, "postgresql": 15432}
REAL_PG_DATA = Path.home() / "Library" / "Application Support" / "Postgres" / "var-18"
REAL_LAUNCHD_PREFIX = "com.companybrain"
TEST_LAUNCHD_PREFIX = "com.companybrain-test"

RESOURCE_KINDS = frozenset(
    {
        "drive",
        "data-dir",
        "exports-dir",
        "system-db",
        "keychain",
        "service-ports",
        "network-probe",
        "wifi",
    }
)

_DB_PATTERN = re.compile(r"(?:^|dbname=|/|\s)company_brain(?=$|[\s?;&'\"])")
_LAUNCHD_PATTERN = re.compile(r"(?<![\w.-])com\.companybrain\.")
_KEYCHAIN_PATTERN = re.compile(r"(?<![\w/.-])company-brain(?!-test)(?!\w)")
_PATH_TOKEN = re.compile(r"(?:~/|/)[^\s'\"=:,;()\[\]{}]*")

_READ_EVENTS = {"os.listdir": 0, "os.scandir": 0, "os.chdir": 0}
_WRITE_EVENTS = {
    "os.mkdir": (0,),
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.rename": (0, 1),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.truncate": (0,),
    "os.link": (0, 1),
    "os.symlink": (0, 1),
    "os.utime": (0,),
    "shutil.rmtree": (0,),
    "shutil.copyfile": (0, 1),
    "shutil.copytree": (0, 1),
    "shutil.move": (0, 1),
}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


class RealResourceError(RuntimeError):
    """A test tried to use a real resource without an allowlist grant."""


@dataclass(frozen=True)
class RealResources:
    """Real paths by resource kind; database, Keychain and ports are fixed names."""

    paths: Mapping[str, tuple[str, ...]]

    @classmethod
    def for_home(cls, home: Path, extra_drive_roots: Iterable[Path] = ()) -> RealResources:
        drive = [home / "Library" / "CloudStorage", Path("/Volumes/GoogleDrive")]
        drive.extend(extra_drive_roots)
        raw = {
            "data-dir": [home / "CompanyBrainData"],
            "exports-dir": [home / "CompanyBrainExports"],
            "drive": drive,
        }
        paths = {
            kind: tuple(sorted({os.path.realpath(p) for p in items} | {str(p) for p in items}))
            for kind, items in raw.items()
        }
        return cls(paths=paths)

    def kind_of_path(self, path: str | bytes | os.PathLike[str] | os.PathLike[bytes]) -> str | None:
        text = os.fsdecode(path)
        candidates = {os.path.abspath(os.path.expanduser(text))}
        candidates.add(os.path.realpath(next(iter(candidates))))
        for kind, roots in self.paths.items():
            for root in roots:
                for candidate in candidates:
                    if candidate == root or candidate.startswith(root.rstrip("/") + "/"):
                        return kind
        return None

    def kinds_in_text(self, text: str) -> set[str]:
        """Resource kinds a command line or environment value refers to."""
        kinds: set[str] = set()
        home = str(Path.home())
        for kind, roots in self.paths.items():
            for root in roots:
                forms = {root}
                if root.startswith(home + "/"):
                    forms.add("~" + root[len(home) :])
                for form in forms:
                    if re.search(re.escape(form) + r"(?=$|[/\s'\":;,)\]}])", text):
                        kinds.add(kind)
        for token in _PATH_TOKEN.findall(text):
            found = self.kind_of_path(token)
            if found:
                kinds.add(found)
        if _DB_PATTERN.search(text):
            kinds.add("system-db")
        if _KEYCHAIN_PATTERN.search(text):
            kinds.add("keychain")
        if _LAUNCHD_PATTERN.search(text):
            kinds.add("service-ports")
        ports = "|".join(str(p) for p in REAL_PORTS.values())
        if re.search(rf"(?:--port[= ]|:)(?:{ports})(?!\d)", text):
            kinds.add("service-ports")
        return kinds


@dataclass(frozen=True)
class AllowlistEntry:
    test: str
    task: str
    resources: frozenset[str]
    writes: frozenset[str]
    reason: str


def load_allowlist(path: Path) -> list[AllowlistEntry]:
    """Read and validate the list of tests that may use real resources."""
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = []
    for raw in data.get("exception", []):
        resources = frozenset(raw["resources"])
        writes = frozenset(raw.get("writes", []))
        unknown = resources - RESOURCE_KINDS
        if unknown:
            raise ValueError(f"{raw['test']}: unknown resources {sorted(unknown)}")
        for write in writes:
            if write.split(":", 1)[0] not in resources or ":" not in write:
                raise ValueError(f"{raw['test']}: write {write!r} must be '<resource>:<what>'")
        entries.append(
            AllowlistEntry(
                test=raw["test"],
                task=raw["task"],
                resources=resources,
                writes=writes,
                reason=raw["reason"],
            )
        )
    return entries


@dataclass
class Grant:
    kinds: frozenset[str]
    writes: frozenset[str]


@dataclass
class Guard:
    resources: RealResources
    allowlist: list[AllowlistEntry] = field(default_factory=list)
    current_test: str | None = None
    grants: list[Grant] = field(default_factory=list)

    def _granted(self, kind: str, write: bool) -> bool:
        for grant in self.grants:
            if kind in grant.kinds and (
                not write or any(w.startswith(kind + ":") for w in grant.writes)
            ):
                return True
        return False

    def deny_unless_granted(self, kind: str, what: str, write: bool = False) -> None:
        if not self._granted(kind, write):
            action = "write to" if write else "use"
            test = self.current_test or "a test process"
            raise RealResourceError(
                f"{test} tried to {action} a real resource ({kind}): {what}. "
                "Only tests listed in config/real_resource_allowlist.toml may, "
                "inside real_resources()."
            )

    def check_path(
        self, path: str | bytes | os.PathLike[str] | os.PathLike[bytes], write: bool
    ) -> None:
        kind = self.resources.kind_of_path(path)
        if kind:
            self.deny_unless_granted(kind, os.fsdecode(path), write)

    def check_text(self, text: str, what: str) -> None:
        for kind in sorted(self.resources.kinds_in_text(text)):
            self.deny_unless_granted(kind, what)

    def check_command(
        self, args: object, env: Mapping[str, str] | None, cwd: object = None
    ) -> None:
        if isinstance(args, (str, bytes, os.PathLike)):
            argv = [os.fsdecode(args)]
        elif isinstance(args, Sequence):
            argv = [
                os.fsdecode(a) if isinstance(a, (str, bytes, os.PathLike)) else "" for a in args
            ]
        else:
            argv = []
        self.check_text(" ".join(argv), f"subprocess {argv[:3]}")
        if cwd is not None and isinstance(cwd, (str, bytes, os.PathLike)):
            self.check_path(cwd, write=False)
        environment = os.environ if env is None else env
        for key, value in environment.items():
            text = os.fsdecode(value) if isinstance(value, (str, bytes)) else str(value)
            self.check_text(text, f"subprocess environment {os.fsdecode(key)}")

    @contextmanager
    def real_resources(self, *kinds: str, writes: Sequence[str] = ()) -> Iterator[None]:
        entry = self._entry_for(self.current_test)
        wanted = frozenset(kinds)
        if entry is None:
            raise RealResourceError(
                f"{self.current_test} is not in config/real_resource_allowlist.toml; "
                "a test marker alone grants nothing."
            )
        if not wanted <= entry.resources:
            raise RealResourceError(
                f"{self.current_test} may use {sorted(entry.resources)}, "
                f"not {sorted(wanted - entry.resources)}."
            )
        if not frozenset(writes) <= entry.writes:
            raise RealResourceError(
                f"{self.current_test} may write only {sorted(entry.writes)}, "
                f"not {sorted(frozenset(writes) - entry.writes)}."
            )
        grant = Grant(kinds=wanted, writes=frozenset(writes))
        self.grants.append(grant)
        previous = os.environ.get(ENV_GRANT)
        os.environ[ENV_GRANT] = json.dumps({"kinds": sorted(wanted), "writes": sorted(writes)})
        try:
            yield
        finally:
            self.grants.remove(grant)
            if previous is None:
                os.environ.pop(ENV_GRANT, None)
            else:
                os.environ[ENV_GRANT] = previous

    def _entry_for(self, test: str | None) -> AllowlistEntry | None:
        if test is None:
            return None
        for entry in self.allowlist:
            if fnmatch.fnmatchcase(test, entry.test):
                return entry
        return None

    def audit(self, event: str, args: tuple[object, ...]) -> None:
        if event == "open":
            path, mode, flags = args[0], args[1], args[2]
            if isinstance(path, (str, bytes, os.PathLike)):
                write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                    isinstance(flags, int) and bool(flags & _WRITE_FLAGS)
                )
                self.check_path(path, write)
        elif event in _READ_EVENTS:
            path = args[_READ_EVENTS[event]]
            if isinstance(path, (str, bytes, os.PathLike)):
                self.check_path(path, write=False)
        elif event in _WRITE_EVENTS:
            for index in _WRITE_EVENTS[event]:
                path = args[index] if index < len(args) else None
                if isinstance(path, (str, bytes, os.PathLike)):
                    self.check_path(path, write=True)
        elif event == "subprocess.Popen":
            env = args[3]
            self.check_command(args[1], env if isinstance(env, Mapping) else None, cwd=args[2])
        elif event == "os.system":
            command = args[0]
            if isinstance(command, (str, bytes)):
                self.check_text(os.fsdecode(command), "os.system")
        elif event in ("os.exec", "os.posix_spawn", "os.spawn"):
            env = args[2] if len(args) > 2 else None
            self.check_command(args[1], env if isinstance(env, Mapping) else None)
        elif event == "socket.connect":
            address = args[1]
            if (
                isinstance(address, tuple)
                and len(address) >= 2
                and address[1] in REAL_PORTS.values()
            ):
                self.deny_unless_granted("service-ports", f"connect {address[:2]}")


_ACTIVE: Guard | None = None
_HOOK_INSTALLED = False
_LOCAL = threading.local()


def _hook(event: str, args: tuple[object, ...]) -> None:
    guard = _ACTIVE
    if guard is None or getattr(_LOCAL, "busy", False):
        return
    _LOCAL.busy = True
    try:
        guard.audit(event, args)
    finally:
        _LOCAL.busy = False


def activate(guard: Guard) -> None:
    """Make `guard` the active guard and install the audit hook once."""
    global _ACTIVE, _HOOK_INSTALLED
    _ACTIVE = guard
    if not _HOOK_INSTALLED:
        sys.addaudithook(_hook)
        _HOOK_INSTALLED = True


def deactivate() -> None:
    global _ACTIVE
    _ACTIVE = None


def active_guard() -> Guard | None:
    return _ACTIVE


def guard_active() -> bool:
    return _ACTIVE is not None


def install_child_guard() -> None:
    """Guard a subprocess of the test suite, with the grant its parent held."""
    if _ACTIVE is not None:
        return
    guard = Guard(resources=RealResources.for_home(Path.home()))
    grant = os.environ.get(ENV_GRANT)
    if grant:
        data = json.loads(grant)
        guard.grants.append(Grant(kinds=frozenset(data["kinds"]), writes=frozenset(data["writes"])))
    activate(guard)


@contextmanager
def real_resources(*kinds: str, writes: Sequence[str] = ()) -> Iterator[None]:
    """Use named real resources; only for tests on the allowlist."""
    guard = _ACTIVE
    if guard is None:
        yield
        return
    with guard.real_resources(*kinds, writes=writes):
        yield


def check_settings(settings: Settings) -> None:
    """Refuse settings that point the test suite at a real resource."""
    guard = _ACTIVE or Guard(resources=RealResources.for_home(Path.home()))
    for path in (settings.root, settings.data_dir, settings.exports_dir):
        if path is not None:
            guard.check_path(path, write=False)
    if settings.database == REAL_DATABASE:
        guard.deny_unless_granted("system-db", f"database {settings.database}")
    if not settings.keychain_prefix.startswith(TEST_KEYCHAIN_PREFIX):
        guard.deny_unless_granted("keychain", f"Keychain prefix {settings.keychain_prefix}")
    for port in (settings.openwebui_port, settings.backend_port):
        if port in REAL_PORTS.values():
            guard.deny_unless_granted("service-ports", f"port {port}")
    for port in (settings.ollama_port, settings.pg_port):
        if port in REAL_SERVICE_PORTS.values():
            guard.deny_unless_granted("service-ports", f"service port {port}")
    if os.path.realpath(settings.pg_data) == os.path.realpath(REAL_PG_DATA):
        guard.deny_unless_granted("system-db", f"database folder {settings.pg_data}")
    if settings.launchd_prefix != TEST_LAUNCHD_PREFIX:
        guard.deny_unless_granted("service-ports", f"launchd prefix {settings.launchd_prefix}")
