"""Create the backup key pair (T39, H7). Run by the owner from "Set up backups".

The public half goes into `config/backup_recipient.txt` (it can only lock backups).
The secret half, the recovery key, is shown once in this window and never stored
on the Mac: the owner saves it in their password manager or on paper.

Backups are also signed (security scan F1): a key derived from the recovery key
goes into the Keychain. When backups were set up before signing existed, this
asks once for the recovery key, in a hidden prompt, to derive that key.
"""

import getpass
import subprocess
import sys
from pathlib import Path

from company_brain import signing

REPO = Path(__file__).resolve().parents[1]
AGE_DIR = Path.home() / ".local" / "opt" / "age-1.3.2"
RECIPIENT = REPO / "config" / "backup_recipient.txt"


def public_key(identity: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed program; the key goes through a pipe
        [str(AGE_DIR / "age-keygen"), "-y"],
        input=identity.strip() + "\n",
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


def enable_signing() -> int:
    if signing.stored_key() is not None:
        print("Backups are already set up. Your recovery key was shown when they were set up.")
        return 0
    print("Backups are now signed too, so a backup made by anyone else is never restored.")
    print("This needs your recovery key once (from your password manager or paper).")
    identity = getpass.getpass("Paste your recovery key (it stays hidden), then press Return: ")
    if public_key(identity) != RECIPIENT.read_text(encoding="utf-8").strip():
        print("That is not the recovery key these backups are locked with. Nothing was changed.")
        return 1
    signing.store_key(signing.key_from_identity(identity))
    print("Done: from tonight, backups are signed. You can close this window.")
    return 0


def main() -> int:
    if RECIPIENT.exists():
        return enable_signing()
    out = subprocess.run(  # noqa: S603 - fixed program
        [str(AGE_DIR / "age-keygen")], capture_output=True, text=True, check=True
    )
    identity = next(line for line in out.stdout.splitlines() if line.startswith("AGE-SECRET-KEY"))
    signing.store_key(signing.key_from_identity(identity))
    RECIPIENT.write_text(public_key(identity) + "\n", encoding="utf-8")
    print("Your backup RECOVERY KEY (shown only now, never stored on this Mac):\n")
    print(f"    {identity}\n")
    print("1. Copy it into your password manager (or write it down) now.")
    print("2. Without it, the backups can never be opened.")
    print("3. Do not paste it into Claude or any chat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
