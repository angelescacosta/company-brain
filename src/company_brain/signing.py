"""Backups are signed as well as encrypted (security scan F1).

age encrypts to the public recipient, which anyone may hold, so a file the recovery
key opens says nothing about who made it. Each backup's manifest (and the encrypted
list of deleted clients next to the backups) therefore carries an HMAC-SHA256 made
with a key derived from the recovery key. Set up backups keeps that derived key in
the Keychain for the nightly backup; restore derives it again from the recovery key
the owner types, so it works on a new Mac with nothing but the saved key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from company_brain import keychain

SERVICE = "company-brain-backup-signing"
ACCOUNT = "backup"
FIELD = "signature"
_LABEL = b"company-brain backup signature v1"


def key_from_identity(identity: str) -> bytes:
    """The signing key for a recovery key; it does not reveal the recovery key."""
    return hmac.new(identity.strip().encode(), _LABEL, hashlib.sha256).digest()


def _mac(key: bytes, data: dict[str, Any]) -> str:
    body = {k: v for k, v in data.items() if k != FIELD}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(key, canonical.encode(), hashlib.sha256).hexdigest()


def signed(key: bytes, data: dict[str, Any]) -> dict[str, Any]:
    return {**data, FIELD: _mac(key, data)}


def verified(key: bytes, data: dict[str, Any]) -> bool:
    given = data.get(FIELD)
    return isinstance(given, str) and hmac.compare_digest(given, _mac(key, data))


def stored_key() -> bytes | None:
    """The key Set up backups kept in the Keychain, or None before it was set up."""
    try:
        return bytes.fromhex(keychain.get_password(SERVICE, ACCOUNT))
    except (keychain.SecretMissing, ValueError):
        return None


def store_key(key: bytes) -> None:
    keychain.set_password(SERVICE, ACCOUNT, key.hex())
