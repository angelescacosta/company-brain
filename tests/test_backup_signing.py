"""Security scan F1: age only encrypts to the public recipient, so anyone could make a backup the
recovery key opens. Each backup's manifest carries a signature made with a key derived from the
recovery key (kept in the Keychain), which restore re-derives from the key the owner types."""

from company_brain import signing

MANIFEST = {"created": "2026-10-25T02:00:00", "files": {"database.dump": "ab" * 32}}


def test_the_signing_key_comes_from_the_recovery_key_alone() -> None:
    first = signing.key_from_identity("AGE-SECRET-KEY-1FAKEONE")
    assert first == signing.key_from_identity("  AGE-SECRET-KEY-1FAKEONE\n")
    assert first != signing.key_from_identity("AGE-SECRET-KEY-1FAKETWO")
    assert "FAKEONE" not in first.hex()


def test_a_signature_checks_the_whole_manifest_and_the_key() -> None:
    key = signing.key_from_identity("AGE-SECRET-KEY-1FAKEONE")
    signed = signing.signed(key, MANIFEST)
    assert signing.verified(key, signed)
    changed = {**signed, "files": {"database.dump": "cd" * 32}}
    assert not signing.verified(key, changed)
    assert not signing.verified(signing.key_from_identity("AGE-SECRET-KEY-1FAKETWO"), signed)
    assert not signing.verified(key, MANIFEST)  # no signature at all
