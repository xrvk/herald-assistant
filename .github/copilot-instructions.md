# Scout Report

Single-file Python Discord bot (`main.py`). ICS calendar aggregation + LLM Q&A + scheduled digests. Docker-deployed.

## Stack

Python 3.11 (Docker) / 3.13 (local). Deps: discord.py, APScheduler, Apprise, icalendar, recurring-ical-events, google-genai. All config via env vars (no dotenv).

## LLM Backends

Ollama (local) or Gemini (cloud) via `LLM_BACKEND` env var. `get_backend()`/`set_backend()` for runtime state. `_get_gemini_client()` is lazy-init. Gemini free tier: ~5 RPM with retry/backoff.

## Conversation History

Per-user/channel in-memory history (`_conv_history` dict of deques). `_get_history()`/`_store_exchange()` helpers. Bot answers truncated to 500 chars before storage. TTL-based staleness (`CONV_HISTORY_TTL`, default 30 min). Ollama: `num_ctx` bumped by `CONV_HISTORY_CTX_BUMP` (default 4096) only when history overflows base window (avoids KV cache reload penalty); oldest exchanges dropped first if token budget exceeded. Gemini: history passed as `types.Content` list, no token concerns. `!backend` switch clears history. DMs keyed by `author.id` (not channel ID).

## Bot Process Management

Use this exact local reboot sequence to avoid duplicate Discord replies:

```bash
# stop local + docker instances
pkill -9 -f "python3 main.py" 2>/dev/null; pkill -9 -f "Python main.py" 2>/dev/null; sleep 1
docker stop scout_report 2>/dev/null || true

# start local bot (override Docker-specific OLLAMA_URL from .env)
set -a && source .env && set +a && export OLLAMA_URL=http://localhost:11434 && .venv/bin/python3 main.py
```

Verify after start:

```bash
ps aux | grep "[p]ython.*main.py"
docker ps --format '{{.Names}}' | grep -x 'scout_report' || true
```

Healthy startup logs must include:
- `Discord bot logged in as ...`
- `Scheduler started.`

## Rules

- Never log full calendar URLs (contain auth tokens). Use `_cal_labels`.
- `.env` is git-ignored with secrets. `.env.example` documents all vars.
- Syntax check: `python3 -c "import ast; ast.parse(open('main.py').read())"`
- Build: `docker compose up -d --build`
- `google-genai` is imported lazily inside `_get_gemini_client()`, not at module top — missing package won't error until Gemini is actually used.
- Discord replies truncated at 1900 chars (2000 limit). Scheduled digests require `APPRISE_URL`.
- `HISTORY_DAYS=0` disables past-event classification entirely. Cache TTLs are in seconds (default 3600).
- At least one calendar URL must be configured or startup crashes.
- Timeouts are hard-coded: calendar fetch 30s, Ollama chat 120s, classification 15s.
