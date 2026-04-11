#!/usr/bin/env bash
# run_tests.sh — Run Scout Report test suite
#
# Usage:
#   ./run_tests.sh              # unit tests only (no bot needed)
#   ./run_tests.sh --live       # unit + live Discord integration tests
#   ./run_tests.sh --live-only  # live integration tests only
#   ./run_tests.sh --benchmark  # Gemini model latency benchmark (needs GEMINI_API_KEY)

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
    --benchmark)
        echo "━━━ Gemini Model Benchmark ━━━"
        set -a && source .env && set +a
        "$PYTEST" tests/test_gemini_models.py -v -s
        ;;
    *)
        echo "━━━ Unit Tests ━━━"
        "$PYTEST" tests/test_unit.py -v
        ;;
esac
