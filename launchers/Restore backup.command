#!/bin/bash
# Restore backup: puts back the newest backup. You will need your recovery key (H7).
cd "$(dirname "$0")/.." || exit 1
scripts/brainctl restore
status=$?
echo
read -r -p "Press Return to close this window. " _
clear
exit "$status"
