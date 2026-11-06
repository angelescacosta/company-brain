"""T6 follow-up: launchd removes a job a moment after `bootout` returns.

Starting the same job again during that moment fails ("Bootstrap failed: 5"),
seen once in the launcher integration test on 21 October.
"""

import subprocess
from pathlib import Path

from company_brain.services import Launchd, Service


class SlowLaunchd(Launchd):
    """Fake launchctl: a job stays loaded for two status checks after bootout."""

    def __init__(self, jobs_dir: Path) -> None:
        super().__init__(jobs_dir)
        self.jobs: dict[str, int] = {}  # label -> remaining checks before it is gone

    def _launchctl(self, *args: str) -> subprocess.CompletedProcess[str]:
        verb, target = args[0], args[-1]
        label = Path(target).stem if verb == "bootstrap" else target.rsplit("/", 1)[-1]
        code = 0
        if verb == "print":
            remaining = self.jobs.get(label)
            if remaining is None:
                code = 113
            elif remaining > 0:
                self.jobs[label] = remaining - 1
                if remaining == 1:
                    del self.jobs[label]
        elif verb == "bootout":
            self.jobs[label] = 2
        elif verb == "bootstrap":
            if label in self.jobs:
                return subprocess.CompletedProcess(args, 5, "", "Bootstrap failed: 5")
            self.jobs[label] = -1  # loaded until booted out
        return subprocess.CompletedProcess(args, code, "", "")


def service(tmp_path: Path) -> Service:
    return Service(
        name="ollama",
        title="Ollama",
        label="com.companybrain-test.ollama",
        program=(str(tmp_path / "ollama"), "serve"),
        env={},
        cwd=tmp_path,
        port=21434,
        command_prefix=str(tmp_path / "ollama"),
    )


def test_start_right_after_stop_waits_for_launchd(tmp_path: Path) -> None:
    launchd = SlowLaunchd(tmp_path / "jobs")
    job = service(tmp_path)
    launchd.start(job)
    launchd.stop(job.label)
    launchd.start(job)
    assert launchd.loaded(job.label)


def test_restart_of_a_loaded_job_waits_for_launchd(tmp_path: Path) -> None:
    launchd = SlowLaunchd(tmp_path / "jobs")
    job = service(tmp_path)
    launchd.start(job)
    launchd.start(job)
    assert launchd.loaded(job.label)
