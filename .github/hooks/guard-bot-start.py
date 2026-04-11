#!/usr/bin/env python3
"""PreToolUse hook: guard the bot reboot sequence.

1. Block starting main.py if .env is sourced without OLLAMA_URL override.
2. Prompt for confirmation if bot instances are already running.
"""
import json
import os
import re
import subprocess
import sys


def _deny(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def _ask(reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def main():
    try:
        inp = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return  # Can't parse input — don't block

    if inp.get("toolName") != "run_in_terminal":
        return

    command = inp.get("toolInput", {}).get("command", "")

    # Only guard commands that look like they're starting main.py
    if not re.search(r"python3?\s+.*main\.py", command):
        return

    # --- Check 1: OLLAMA_URL override when sourcing .env ---
    sources_env = bool(re.search(r"source\s+\.env|\.\s+\.env", command))
    has_ollama_override = "OLLAMA_URL=http://localhost:11434" in command

    if sources_env and not has_ollama_override:
        _deny(
            "Blocked: command sources .env but does not override OLLAMA_URL. "
            ".env contains the Docker-internal Ollama URL which won't work locally. "
            "Add: export OLLAMA_URL=http://localhost:11434"
        )
        return

    # --- Check 2: existing running instances ---
    my_pid = str(os.getpid())

    existing_procs = 0
    try:
        result = subprocess.run(
            ["pgrep", "-f", "python.*main.py"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.stdout.strip():
            pids = [p for p in result.stdout.strip().splitlines() if p != my_pid]
            existing_procs = len(pids)
    except Exception:
        pass

    docker_count = 0
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        containers = [
            n
            for n in result.stdout.strip().splitlines()
            if n in ("scout_report", "context_bot")
        ]
        docker_count = len(containers)
    except Exception:
        pass

    if existing_procs > 0 or docker_count > 0:
        _ask(
            f"{existing_procs} python main.py process(es) and "
            f"{docker_count} Docker container(s) already running. "
            "Starting another instance may cause duplicate Discord replies. "
            "Stop them first: pkill -9 -f 'python3 main.py'; pkill -9 -f 'Python main.py'; "
            "docker stop scout_report context_bot 2>/dev/null"
        )


if __name__ == "__main__":
    main()
