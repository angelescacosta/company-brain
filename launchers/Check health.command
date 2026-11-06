#!/bin/bash
# Check health: Reports each part of the AI in plain English.
cd "$(dirname "$0")/.." || exit 1
scripts/brainctl health
status=$?
echo
read -r -p "Press Return to close this window. " _
exit "$status"
