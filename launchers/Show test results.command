#!/bin/bash
# Show test results: opens the results page (rebuilt first if the results changed).
cd "$(dirname "$0")/.." || exit 1
scripts/brainctl results
status=$?
sleep 2
exit "$status"
