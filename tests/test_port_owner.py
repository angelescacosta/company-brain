"""Security scan F4: a service counts as running only when the program listening on its port
is that service's own program, run by the owner; another account can bind a loopback port."""

import os

import pytest

from company_brain import services
from company_brain.settings import Settings


@pytest.fixture
def webui() -> services.Service:
    return next(s for s in services.define(Settings.from_env()) if s.name == "openwebui")


@pytest.mark.parametrize(
    ("found", "uid", "expected"),
    [
        ([("python3", 4321, "127.0.0.1", 0)], os.getuid(), True),
        ([("python3", 4321, "127.0.0.1", 0)], os.getuid() + 1, False),  # another account
        ([("nc", 4321, "127.0.0.1", 0)], os.getuid(), False),  # another program
        ([], os.getuid(), False),  # not visible: another account's process
    ],
)
def test_only_the_owners_own_program_counts(
    webui: services.Service,
    monkeypatch: pytest.MonkeyPatch,
    found: list[tuple[str, int, str, int]],
    uid: int,
    expected: bool,
) -> None:
    monkeypatch.setattr(services, "listening", lambda _port: found)
    monkeypatch.setattr(services, "uid_of", lambda _pid: uid)
    assert services.owned(webui) is expected


def test_a_port_answered_by_someone_else_is_not_ready(
    webui: services.Service, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(services, "_http_ok", lambda _port: True)
    monkeypatch.setattr(services, "owned", lambda _service: False)

    class Connected:
        def __enter__(self) -> "Connected":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(services.socket, "create_connection", lambda *_a, **_k: Connected())
    assert not services.ready(webui)
