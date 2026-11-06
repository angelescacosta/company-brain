#!/bin/bash
# Stop AI: Stops the AI services and frees their memory.
cd "$(dirname "$0")/.." || exit 1
scripts/brainctl stop
status=$?
echo
read -r -p "Press Return to close this window. " _
exit "$status"
