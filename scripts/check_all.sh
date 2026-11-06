#!/bin/bash
# Standard checks CHK1-CHK5 (PRD, "Standard checks and test isolation").
set -euo pipefail
cd "$(dirname "$0")/.."

shopt -s nullglob
shell_files=(launchers/*.command scripts/*.sh)
shopt -u nullglob

echo "CHK1 ruff check";  uv run ruff check .
echo "CHK2 ruff format"; uv run ruff format --check .
echo "CHK3 mypy";        uv run mypy src
echo "CHK4 shellcheck";  uv run shellcheck "${shell_files[@]}"
echo "CHK5 pytest";      uv run pytest -q -m "not slow and not destructive and not release"
echo "All checks passed"
