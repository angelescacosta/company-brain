#!/bin/bash
# T46.AC4: secrets in the working tree and in every commit on every ref (see secret_scan.py).
# Usage: scripts/secret_scan.sh [REPOSITORY]   (default: this project)
here="$(cd "$(dirname "$0")/.." && pwd)"
exec "$HOME/.local/bin/uv" run --quiet --no-project --python 3.13 python "$here/scripts/secret_scan.py" "$@"
