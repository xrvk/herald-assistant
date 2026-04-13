#!/usr/bin/env bash
# run_tests.sh — Run Scout Report test suite
#
# Usage:
#   ./run_tests.sh    # unit tests

set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv/bin"
PYTEST="${VENV}/pytest"

# Ensure pytest is installed
if [[ ! -x "$PYTEST" ]]; then
    echo "Installing pytest..."
    "${VENV}/pip" install pytest -q
fi

echo "━━━ Unit Tests ━━━"
"$PYTEST" tests/test_unit.py -v
