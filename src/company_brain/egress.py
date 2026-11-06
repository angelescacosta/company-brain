"""Firewall probes through the real blocked programs, and LuLu's state (T4, T6).

LuLu keeps no log of its decisions, so a denial counts as recorded when LuLu
holds an enabled block rule for the program and the attempt failed the way
LuLu fails it (D28). Each probe has a control: the owner's curl to the same
address family and destination.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import sys
import textwrap
from collections.abc import Mapping
from pathlib import Path

from company_brain.firewall import (
    LULU_PREFERENCES,
    LULU_RULES,
    NETWORK_EXTENSIONS,
    ProbeResult,
    coverage_problems,
    lulu_filter_enabled,
    lulu_rules,
    settings_problems,
)

PYTHON = os.path.realpath(sys.executable)
OLLAMA = os.path.realpath(Path.home() / ".local/bin/ollama")
POSTGRES = "/Applications/Postgres.app/Contents/Versions/18/bin/postgres"
TARGETS = {
    "ipv4": ("1.1.1.1", "https://1.1.1.1/", "-4"),
    "ipv6": ("2606:4700:4700::1111", "https://[2606:4700:4700::1111]/", "-6"),
}
REGISTRY = "https://registry.ollama.ai/v2/"
# Not installed here, so a pull must contact the registry.
PROBE_MODEL = "all-minilm:22m"
EBADF = 9  # LuLu refuses a blocked connect() with "Bad file descriptor".

CONNECT = textwrap.dedent(
    """
    import json, socket, sys
    family = socket.AF_INET6 if ":" in sys.argv[1] else socket.AF_INET
    s = socket.socket(family)
    s.settimeout(20)
    try:
        s.connect((sys.argv[1], 443))
        print(json.dumps({"connected": True, "errno": None}))
    except OSError as error:
        print(json.dumps({"connected": False, "errno": error.errno}))
    """
)
# The same attempt made by a child process of the blocked interpreter.
VIA_CHILD = textwrap.dedent(
    """
    import subprocess, sys
    result = subprocess.run([sys.executable, "-I", "-c", sys.argv[1], sys.argv[2]],
                            capture_output=True, text=True, check=True)
    print(result.stdout, end="")
    """
)


def control(url: str, flag: str, seconds: int = 20) -> bool:
    code = subprocess.run(  # noqa: S603 - fixed programs, arguments built here
        [
            "/usr/bin/curl",
            flag,
            "-s",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "--max-time",
            str(seconds),
            url,
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return code not in ("", "000")


def internet_reachable() -> bool:
    return control(TARGETS["ipv4"][1], "-4", seconds=5)


def blocked(path: str) -> bool:
    return coverage_problems({path: path}, lulu_rules(LULU_RULES.read_bytes())) == []


def python_probe(name: str, address: str, url: str, flag: str, via_child: bool) -> ProbeResult:
    args = (
        [PYTHON, "-I", "-c", VIA_CHILD, CONNECT, address]
        if via_child
        else [PYTHON, "-I", "-c", CONNECT, address]
    )
    # Fixed programs, arguments built here.
    output = subprocess.run(args, capture_output=True, text=True, check=True, timeout=120)  # noqa: S603
    result = json.loads(output.stdout)
    return ProbeResult(
        name=name,
        control_ok=control(url, flag),
        connected=result["connected"],
        deny_recorded=result["errno"] == EBADF and blocked(PYTHON),
    )


def ollama_probe(ollama_host: str) -> ProbeResult:
    # The control runs first: after a blocked attempt, macOS answers lookups
    # for the same name slowly for a while, which would fail the control.
    control_ok = control(REGISTRY, "-4")
    env = {**os.environ, "OLLAMA_HOST": ollama_host}
    try:
        output = subprocess.run(  # noqa: S603 - fixed programs, arguments built here
            [OLLAMA, "pull", PROBE_MODEL],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
            env=env,
        )
        text, finished_ok = output.stdout + output.stderr, output.returncode == 0
    except subprocess.TimeoutExpired as expired:
        text, finished_ok = str(expired.stdout or "") + str(expired.stderr or ""), False
    listed = (
        PROBE_MODEL
        in subprocess.run(  # noqa: S603 - fixed programs, arguments built here
            [OLLAMA, "list"], capture_output=True, text=True, check=True, env=env
        ).stdout
    )
    if listed:
        subprocess.run(  # noqa: S603 - fixed program
            [OLLAMA, "rm", PROBE_MODEL], capture_output=True, check=True, env=env
        )
    # Only LuLu's refusal counts; a failed name lookup could be the network.
    refused = re.search(r"connect: bad file descriptor", text)
    return ProbeResult(
        name="ollama-pull",
        control_ok=control_ok,
        connected=finished_ok or listed,
        deny_recorded=bool(refused) and blocked(OLLAMA),
    )


def run_probes(ollama_host: str) -> list[ProbeResult]:
    probes = [ollama_probe(ollama_host)]
    for family, (address, url, flag) in TARGETS.items():
        probes.append(python_probe(f"python-{family}", address, url, flag, False))
        probes.append(python_probe(f"child-{family}", address, url, flag, True))
    return probes


def lulu_running() -> bool:
    found = subprocess.run(  # noqa: S603 - fixed programs, arguments built here
        ["/usr/bin/pgrep", "-f", "com.objective-see.lulu.extension"],
        capture_output=True,
        check=False,
    )
    return found.returncode == 0


def lulu_problems(executables: Mapping[str, str]) -> list[str]:
    """LuLu is running and filtering, its settings are pinned, and each program is blocked."""
    if not lulu_running():
        return ["The LuLu firewall is not running."]
    found = []
    if not lulu_filter_enabled(NETWORK_EXTENSIONS.read_bytes()):
        found.append("LuLu's network filter is switched off in System Settings.")
    found += settings_problems(plistlib.loads(LULU_PREFERENCES.read_bytes()))
    found += coverage_problems(executables, lulu_rules(LULU_RULES.read_bytes()))
    return found
