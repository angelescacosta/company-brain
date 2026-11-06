#!/bin/bash
# T46: the automated parts of the security audit (see security_audit.py).
# Exit codes: 0 nothing open, 1 open findings, 2 a scanner or setup error.
here="$(cd "$(dirname "$0")/.." && pwd)"
exec "$HOME/.local/bin/uv" run --quiet --no-project --python 3.13 python "$here/scripts/security_audit.py"
