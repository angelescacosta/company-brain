#!/bin/bash
# Start AI: Checks the firewall, starts the AI services and opens Open WebUI.
cd "$(dirname "$0")/.." || exit 1
scripts/brainctl start
status=$?
echo
read -r -p "Press Return to close this window. " _
exit "$status"
