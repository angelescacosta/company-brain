"""Shared helpers for integration tests."""

import os
import subprocess
import sys
import textwrap
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Runs first in the child: Hugging Face offline mode, and an audit hook that
# refuses any connection or name lookup that is not on this Mac.
OFFLINE_PRELUDE = textwrap.dedent(
    """
    import socket, sys

    LOCAL = {"127.0.0.1", "::1", "localhost"}

    def _deny(event, args):
        if event == "socket.connect":
            address = args[1]
            if isinstance(address, tuple) and address[0] not in LOCAL:
                raise ConnectionRefusedError(f"offline test: blocked {address[0]}")
        if event == "socket.getaddrinfo" and args[0] not in LOCAL and args[0] is not None:
            raise ConnectionRefusedError(f"offline test: blocked lookup {args[0]}")

    sys.addaudithook(_deny)
    """
)


def offline_env() -> dict[str, str]:
    models = tomllib.loads((REPO / "config" / "models.toml").read_text())
    return {
        **os.environ,
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "TIKTOKEN_CACHE_DIR": str(Path(models["tiktoken"]["cache_dir"]).expanduser()),
        "TOKENIZERS_PARALLELISM": "false",
    }


@pytest.fixture
def run_offline() -> Callable[[str], subprocess.CompletedProcess[str]]:
    """Run Python code in a child process that cannot reach the internet."""

    def run(code: str, timeout: float = 900) -> subprocess.CompletedProcess[str]:
        source = OFFLINE_PRELUDE + "\n" + textwrap.dedent(code)
        return subprocess.run(
            [sys.executable, "-c", source],
            capture_output=True,
            text=True,
            env=offline_env(),
            cwd=REPO,
            timeout=timeout,
        )

    return run
