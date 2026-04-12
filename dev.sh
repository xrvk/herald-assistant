#!/usr/bin/env bash
# dev.sh — Fast local dev restart for Scout Report
#
# Usage:
#   ./dev.sh            stop existing instances + start fresh
#   ./dev.sh --watch    same, then auto-restart when main.py changes
#   ./dev.sh --stop     stop all instances (no start)

set -uo pipefail
cd "$(dirname "$0")"

PYTHON=".venv/bin/python3"

# ── Helpers ──────────────────────────────────────────────────────────────────

_pids_of_bot() {
    # Match python processes where main.py is the script argument (not a flag value)
    pgrep -f "python[^ ]* .*[/ ]main\.py\b" 2>/dev/null ||
    pgrep -f "python[^ ]* main\.py\b"        2>/dev/null || true
}

_stop() {
    local pids
    pids=$(_pids_of_bot)

    # Stop Docker containers in the background so both run in parallel
    if command -v docker &>/dev/null; then
        docker stop scout_report 2>/dev/null &
        docker stop context_bot  2>/dev/null &
    fi

    if [[ -n "$pids" ]]; then
        # Graceful SIGTERM first
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true

        # Poll up to 2 s for clean exit (0.1 s intervals = 20 checks)
        local i=0
        while [[ -n "$(_pids_of_bot)" ]] && (( i < 20 )); do
            sleep 0.1
            i=$(( i + 1 ))
        done

        # Force-kill anything still alive
        pids=$(_pids_of_bot)
        if [[ -n "$pids" ]]; then
            # shellcheck disable=SC2086
            kill -9 $pids 2>/dev/null || true
        fi
        echo "⏹  Bot stopped."
    else
        echo "⏹  No running bot found."
    fi

    wait 2>/dev/null || true   # collect background docker-stop jobs
}

_load_env() {
    if [[ -f ".env" ]]; then
        # shellcheck source=/dev/null
        set -a; source ".env"; set +a
    fi
    export OLLAMA_URL="http://localhost:11434"   # override Docker's host.docker.internal
}

_syntax_ok() {
    local err
    if err=$("$PYTHON" -c "import ast; ast.parse(open('main.py').read())" 2>&1); then
        return 0
    else
        echo "❌  Syntax error in main.py — fix it first."
        echo "$err"
        return 1
    fi
}

_start() {
    _syntax_ok || return 1
    echo "🚀  Starting Scout Report…  (Ctrl+C to stop)"
    echo "    Watch for: 'Discord bot logged in' + 'Scheduler started.'"
    echo ""
    exec "$PYTHON" main.py
}

_watch() {
    echo "👁  Watch mode — auto-restarts on main.py changes  (Ctrl+C to quit)"
    echo ""

    local bot_pid=""
    _cleanup() {
        echo ""; echo "⏹  Shutting down…"
        [[ -n "$bot_pid" ]] && kill "$bot_pid" 2>/dev/null || true
        exit 0
    }
    trap _cleanup INT TERM

    while true; do
        if ! _syntax_ok; then
            echo "  Waiting for a fix…"
            sleep 2
            continue
        fi

        echo "🚀  Starting bot…"
        "$PYTHON" main.py &
        bot_pid=$!

        # Pure-Python file watcher — no extra dependencies needed
        "$PYTHON" - <<'PY' || true
import os, sys, time
watched_file = "main.py"
mtime = os.path.getmtime(watched_file)
print("  [watch] monitoring main.py", flush=True)
while True:
    time.sleep(0.5)
    try:
        if os.path.getmtime(watched_file) != mtime:
            print("\n  [watch] main.py changed — restarting…", flush=True)
            sys.exit(0)
    except OSError:
        pass
PY

        # Watcher exited — either file changed or Ctrl+C (INT trap handles the latter)
        echo "🔄  Restarting…"
        kill "$bot_pid" 2>/dev/null || true
        wait "$bot_pid" 2>/dev/null || true
        sleep 0.3
    done
}

# ── Dispatch ─────────────────────────────────────────────────────────────────

case "${1:-}" in
    --watch|-w)
        _stop; _load_env; _watch
        ;;
    --stop|-s)
        _stop
        ;;
    --help|-h)
        echo "Usage: ./dev.sh [--watch | --stop]"
        echo "  (no args)   stop existing bot + start fresh"
        echo "  --watch     start + auto-restart when main.py changes"
        echo "  --stop      stop all instances (no start)"
        ;;
    *)
        _stop; _load_env; _start
        ;;
esac
