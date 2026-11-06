#!/bin/bash
# Update software: newer Python packages and Open WebUI, checked; undone if anything fails.
cd "$(dirname "$0")/.." || exit 1
scripts/brainctl update-software
status=$?
echo
read -r -p "Press Return to close this window. " _
clear
exit "$status"
