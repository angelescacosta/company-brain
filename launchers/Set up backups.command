#!/bin/bash
# Set up backups: creates the backup key and shows your recovery key once (H7), or, when
# backups were set up before signing existed, asks for the recovery key once to sign them.
cd "$(dirname "$0")/.." || exit 1
"$HOME/.local/bin/uv" run --quiet python scripts/setup_backup_key.py
status=$?
echo
read -r -p "Press Return to close this window (save the recovery key first if one is shown). " _
clear
exit "$status"
