"""Seal the locked test questions (T14S).

The locked questions are encrypted with `age` to `eval/locked.json.age`. The age
identity (the key) lives only in the Keychain and is never written to a file;
it reaches `age` through a pipe. Every read of the key goes through
`read_key()`, which logs the time and purpose to `reports/seal_access.log`
first. This is a cooperative guard: it stops accidental use during tuning, not
a deliberate read by the agent that wrote the questions. The key is
deliberately not backed up.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from company_brain.keychain import SECURITY, get_password
from company_brain.settings import Settings

AGE_DIR = Path.home() / ".local" / "opt" / "age-1.3.2"
AGE, AGE_KEYGEN = AGE_DIR / "age", AGE_DIR / "age-keygen"
REPO = Path(__file__).resolve().parents[2]
LOG = REPO / "reports" / "seal_access.log"
ACCOUNT = "locked-questions"


def _service() -> str:
    return f"{Settings.from_env().keychain_prefix}-seal"


def _store_key(account: str, key: str) -> None:
    # Interactive mode reads the command from stdin, so the key never shows in the process list.
    subprocess.run(  # noqa: S603 - fixed program
        [SECURITY, "-i"],
        input=f"add-generic-password -U -s {_service()} -a {account} -w {key}\n",
        text=True,
        capture_output=True,
        check=True,
    )


def forget_key(account: str) -> None:
    subprocess.run(  # noqa: S603 - fixed program
        [SECURITY, "delete-generic-password", "-s", _service(), "-a", account],
        capture_output=True,
        check=False,
    )


def read_key(purpose: str, account: str = ACCOUNT, log: Path = LOG) -> str:
    """The only way to the key: the read is logged (time, purpose, process) before it happens."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as handle:
        handle.write(
            f"{datetime.now().isoformat(timespec='seconds')} | {purpose} | pid {os.getpid()}\n"
        )
    return get_password(_service(), account)


def seal(plain: Path, out: Path, account: str = ACCOUNT, log: Path = LOG) -> str:
    """Encrypt `plain` to `out` with a new key kept in the Keychain; verify; return the hash."""
    generated = subprocess.run([str(AGE_KEYGEN)], capture_output=True, text=True, check=True)  # noqa: S603
    key = next(line for line in generated.stdout.splitlines() if line.startswith("AGE-SECRET-KEY"))
    recipient = subprocess.run(  # noqa: S603
        [str(AGE_KEYGEN), "-y"], input=key + "\n", capture_output=True, text=True, check=True
    ).stdout.strip()
    _store_key(account, key)
    subprocess.run(  # noqa: S603
        [str(AGE), "-r", recipient, "-o", str(out), str(plain)], capture_output=True, check=True
    )
    if open_sealed(out, "verify at sealing (T14S)", account, log) != json.loads(
        plain.read_text(encoding="utf-8")
    ):
        raise RuntimeError("the sealed file does not open to the reviewed questions")
    return hashlib.sha256(out.read_bytes()).hexdigest()


def open_sealed(sealed: Path, purpose: str, account: str = ACCOUNT, log: Path = LOG) -> Any:
    key = read_key(purpose, account, log)
    result = subprocess.run(  # noqa: S603
        [str(AGE), "-d", "-i", "/dev/stdin", str(sealed)],
        input=(key + "\n").encode(),
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def plain_copies(texts: list[str], folders: list[Path]) -> list[Path]:
    """Files under `folders` that still hold the text of any locked question."""
    found = []
    for folder in folders:
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            try:
                content = path.read_bytes().decode("utf-8", errors="ignore")
            except OSError:
                continue
            if any(text in content for text in texts):
                found.append(path)
    return found
