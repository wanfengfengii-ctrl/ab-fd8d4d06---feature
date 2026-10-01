#!/bin/sh
# One-shot verification entrypoint for the "verify" compose service.
# Runs only after the app service is healthy (compose service_healthy gate).
# Exits non-zero on the first failed stage.

set -eu

# Resolve the repository root: parent of this script's directory.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
cd "$SCRIPT_DIR/.."

PYTHON=${PYTHON:-}
if [ -z "$PYTHON" ]; then
  if command -v python >/dev/null 2>&1; then PYTHON=python
  else PYTHON=python3; fi
fi

echo "== verify [1/3] unit + differential tests =="
$PYTHON -m unittest discover -s tests -v

echo
echo "== verify [2/3] build check (compileall) =="
$PYTHON -m compileall -q -f app tests verify
echo "compileall OK"

echo
echo "== verify [3/3] wide-offset-interval API smoke =="
$PYTHON verify/smoke_api.py

echo
echo "== verify: ALL STAGES PASSED =="
