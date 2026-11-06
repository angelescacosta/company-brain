"""Passwords live in the macOS Keychain, never in files (T5)."""

from __future__ import annotations

import secrets
import subprocess

SECURITY = "/usr/bin/security"


class SecretMissing(LookupError):
    """No Keychain entry for this service and account."""


def get_password(service: str, account: str) -> str:
    result = subprocess.run(  # noqa: S603
        [SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SecretMissing(f"No Keychain entry for {service} / {account}")
    return result.stdout.rstrip("\n")


def ensure_password(service: str, account: str) -> str:
    """Return the stored password, creating a random one if there is none."""
    try:
        return get_password(service, account)
    except SecretMissing:
        set_password(service, account, secrets.token_urlsafe(32))
        return get_password(service, account)


def set_password(service: str, account: str, password: str) -> None:
    """Store or replace a Keychain entry (letters, digits, '-' and '_' only)."""
    if not password or not all(c.isalnum() or c in "-_" for c in password):
        raise ValueError("unexpected characters in the value to store")
    # Interactive mode reads the command from stdin, so the password never
    # appears in the process list.
    subprocess.run(  # noqa: S603
        [SECURITY, "-i"],
        input=f"add-generic-password -U -s {service} -a {account} -w {password}\n",
        text=True,
        capture_output=True,
        check=True,
    )
