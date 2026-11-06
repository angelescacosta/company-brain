"""T4.AC2: the services listen only on 127.0.0.1, and nothing else listens on
the loopback ports they use. Reads the listening sockets with lsof (read-only)."""

import subprocess

import pytest

from company_brain.firewall import SERVICE_PORTS, listeners

pytestmark = pytest.mark.integration


def test_lsof_parser() -> None:
    sample = (
        "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\n"
        "ollama 10 me 4u IPv4 0x1 0t0 TCP 127.0.0.1:11434 (LISTEN)\n"
        "python3.1 11 me 9u IPv6 0x2 0t0 TCP *:8080 (LISTEN)\n"
    )
    assert listeners(sample) == [
        ("ollama", 10, "127.0.0.1", 11434),
        ("python3.1", 11, "*", 8080),
    ]


@pytest.mark.parametrize("service", sorted(SERVICE_PORTS))
def test_service_listens_only_on_loopback(service: str) -> None:
    port, command_prefix = SERVICE_PORTS[service]
    output = subprocess.run(
        ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], capture_output=True, text=True, check=False
    ).stdout
    on_port = [entry for entry in listeners(output) if entry[3] == port]
    assert on_port, f"{service} is not running"
    for command, _pid, address, _ in on_port:
        assert address == "127.0.0.1", f"{service}: {command} listens on {address}"
        assert command.startswith(command_prefix), f"another program ({command}) uses {port}"
