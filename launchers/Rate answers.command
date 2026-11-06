#!/bin/bash
# Rate answers: opens the rating page; after "Save my scores", press Return here (H6).
cd "$(dirname "$0")/.." || exit 1
scripts/brainctl rate
status=$?
echo
read -r -p "Press Return to close this window. " _
exit "$status"
