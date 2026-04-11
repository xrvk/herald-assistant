#!/usr/bin/env bash
# run_tests.sh — Run Scout Report test suite
#
# Usage:
#   ./run_tests.sh              # unit tests only (no bot needed)
#   ./run_tests.sh --live       # unit + live Discord integration tests
#   ./run_tests.sh --live-only  # live integration tests only

set -euo pipefail
cd "$(dirname "$0")"

VENV=".venv/bin"
PYTEST="${VENV}/pytest"

# Ensure pytest is installed
if [[ ! -x "$PYTEST" ]]; then
    echo "Installing pytest..."
    "${VENV}/pip" install pytest -q
fi

case "${1:-}" in
    --live)
        echo "━━━ Unit Tests ━━━"
        "$PYTEST" tests/test_unit.py -v
        echo ""
        echo "━━━ Live Integration Tests ━━━"
        set -a && source .env && set +a
        "${VENV}/python" tests/test_integration.py
        ;;
    --live-only)
        echo "━━━ Live Integration Tests ━━━"
        set -a && source .env && set +a
        "${VENV}/python" tests/test_integration.py
        ;;
    *)
        echo "━━━ Unit Tests ━━━"
        "$PYTEST" tests/test_unit.py -v
        ;;
esac
