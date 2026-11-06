"""T20.AC1 PBT: after any sequence of events, every index matches the model.

Events: add, change, rename, delete, unavailable (online-only), scan error
(one client folder unreadable), missing root, new index, and ingestion runs.
The model: an index holds every reachable file with its current content;
an unreachable file keeps its last indexed content; a file disappears only
after a complete scan with the root present. This protects against stale,
empty or mass-deleted indexes.
"""

import hashlib
import shutil
from pathlib import Path

import psycopg
import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, consumes, invariant, rule
from ingest_helpers import fake_target, fresh_db, plain_read, vtt

from company_brain import ingest, scanner
from company_brain.availability import SF_DATALESS
from company_brain.embedding import load_embedding_models

pytestmark = pytest.mark.integration

MODEL = load_embedding_models()["bge-m3:latest"]
CLIENTS = ["Acme", "Brisa"]
TEXT = st.lists(
    st.sampled_from(["fee", "EUR 48,000", "pilot", "Leeds", "señal", "plazo", "report"]),
    min_size=3,
    max_size=30,
).map(" ".join)


class Ingestion(RuleBasedStateMachine):
    files = Bundle("files")

    def __init__(self) -> None:
        super().__init__()
        self.conn: psycopg.Connection = fresh_db()
        self.base = Path(self.tmp.mkdtemp())
        self.root = self.base / "root"
        self.root.mkdir()
        self.disk: dict[str, str] = {}  # relative path -> content
        self.offline: set[str] = set()
        self.targets = [fake_target(self.conn, MODEL, "d1", 500)]
        self.expected: dict[str, dict[str, str]] = {self.targets[0].index_id: {}}
        self.counter = 0
        self.root_missing = False

    tmp = __import__("tempfile")

    def _path(self, client: str) -> str:
        self.counter += 1
        return f"Clients/{client}/Reports/doc{self.counter}.vtt"

    def _write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.disk[relative] = content

    @rule(target=files, client=st.sampled_from(CLIENTS), content=TEXT)
    def add(self, client: str, content: str) -> str:
        relative = self._path(client)
        self._write(relative, vtt(content + f" #{self.counter}"))
        return relative

    @rule(relative=files, content=TEXT)
    def change(self, relative: str, content: str) -> None:
        if relative in self.disk:
            self.counter += 1
            self._write(relative, vtt(content + f" #{self.counter}"))

    @rule(target=files, relative=consumes(files))
    def rename(self, relative: str) -> str:
        if relative not in self.disk:
            return relative
        client = relative.split("/")[1]
        new = self._path(client)
        (self.root / new).parent.mkdir(parents=True, exist_ok=True)
        (self.root / relative).rename(self.root / new)
        self.disk[new] = self.disk.pop(relative)
        if relative in self.offline:
            self.offline.discard(relative)
            self.offline.add(new)
        return new

    @rule(relative=consumes(files))
    def delete(self, relative: str) -> None:
        if relative in self.disk:
            (self.root / relative).unlink()
            del self.disk[relative]
            self.offline.discard(relative)

    @rule(relative=files, offline=st.booleans())
    def availability(self, relative: str, offline: bool) -> None:
        if relative in self.disk:
            (self.offline.add if offline else self.offline.discard)(relative)

    @rule(budget=st.sampled_from([300, 800]))
    def new_index(self, budget: int) -> None:
        if len(self.targets) < 3:
            target = fake_target(self.conn, MODEL, f"d{len(self.targets) + 1}", budget)
            if target.index_id not in self.expected:
                self.targets.append(target)
                self.expected[target.index_id] = {}

    def _stat(self, path: str):  # type: ignore[no-untyped-def]
        result = __import__("os").stat(path)
        relative = Path(path).resolve().relative_to(self.root.resolve()).as_posix()
        if relative in self.offline:
            return _Flags(result, SF_DATALESS)
        return result

    @rule(broken=st.sampled_from([None, *CLIENTS]), missing_root=st.booleans())
    def run(self, broken: str | None, missing_root: bool) -> None:
        if missing_root:
            self.root.rename(self.base / "away")

        def scan(root: Path, **kwargs):  # type: ignore[no-untyped-def]
            report = scanner.scan(root, **kwargs)
            if broken is not None:
                report.files = [f for f in report.files if f.client != broken]
                report.errors.append((Path(f"Clients/{broken}"), "could not be listed"))
            return report

        result = ingest.ingest(
            self.conn,
            self.root,
            self.targets,
            plain_read(self.root),
            scan_fn=scan,
            stat=self._stat,
            sleep=lambda _s: None,
        )
        if missing_root:
            (self.base / "away").rename(self.root)
            assert result.state == "failed"
            return
        complete = broken is None
        for target in self.targets:
            model = self.expected[target.index_id]
            for relative, content in self.disk.items():
                if relative in self.offline:
                    continue
                if broken is not None and relative.split("/")[1] == broken:
                    continue
                model[relative] = hashlib.sha256(content.encode()).hexdigest()
            if complete:
                for relative in list(model):
                    if relative not in self.disk:
                        del model[relative]
        # A renamed file found while its old path is still indexed keeps one entry per path.

    @invariant()
    def indexes_match_the_model(self) -> None:
        for target in self.targets:
            assert ingest.indexed(self.conn, target.index_id) == self.expected[target.index_id]

    def teardown(self) -> None:
        self.conn.close()
        shutil.rmtree(self.base, ignore_errors=True)


class _Flags:
    def __init__(self, inner, flags: int) -> None:  # type: ignore[no-untyped-def]
        self._inner, self.st_flags = inner, flags

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)


Ingestion.TestCase.settings = settings(
    max_examples=80,
    stateful_step_count=15,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
TestIngestion = Ingestion.TestCase
