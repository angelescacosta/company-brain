#!/bin/bash
# Update documents: reads new and changed documents into the assistant now.
cd "$(dirname "$0")/.." || exit 1
scripts/brainctl update --trigger launcher
status=$?
echo
read -r -p "Press Return to close this window. " _
exit "$status"
