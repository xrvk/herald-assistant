# Scout Report

Multi-file Python bot (`scout_report/` package). ICS calendar aggregation + LLM Q&A + scheduled digests. Discord + Signal interactive chat. Docker-deployed.

## Architecture

```
scout_report/
├── config.py          # All env var parsing, validation, constants
├── calendar.py        # Fetching, caching, event parsing, context building
├── llm.py             # Ollama/Gemini backends, classification, conversation history
├── notifications.py   # Apprise setup, scheduled digests, Ollama keep-alive
├── chat.py            # Shared pipeline: process_question(), handle_backend_command()
├── discord_bot.py     # Discord client, on_ready, on_message
└── signal_bot.py      # Signal WebSocket receiver, reply sender
main.py                # Thin entry point (~40 lines)
```

Dependency flow: config ← calendar, llm ← chat ← discord_bot, signal_bot. No cycles.

## Stack

Python 3.11 (Docker) / 3.13 (local). Deps: discord.py, APScheduler, Apprise, icalendar, recurring-ical-events, google-genai, websockets. All config via env vars (no dotenv).

## LLM Backends

Ollama (local) or Gemini (cloud) via `LLM_BACKEND` env var. `get_backend()`/`set_backend()` for runtime state. `_get_gemini_client()` is lazy-init. Gemini free tier: ~5 RPM with retry/backoff.

## Conversation History

Per-user/channel in-memory history (`_conv_history` dict of deques in `llm.py`). `_get_history()`/`_store_exchange()` helpers. Bot answers truncated to 500 chars before storage. TTL-based staleness (`CONV_HISTORY_TTL`, default 30 min). Ollama: `num_ctx` bumped by `CONV_HISTORY_CTX_BUMP` (default 4096) only when history overflows base window (avoids KV cache reload penalty); oldest exchanges dropped first if token budget exceeded. Gemini: history passed as `types.Content` list, no token concerns. `!backend` switch clears history. Discord DMs keyed by `(author.id, author.id)`. Signal keyed by `("signal", phone_number)`.

## Signal Interactive Chat

Real-time DM chat via WebSocket to signal-cli REST API sidecar. Enabled by `SIGNAL_CHAT=1` + `SIGNAL_CLI_REST_API_URL` + `SIGNAL_FROM_NUMBER`. Only `SIGNAL_TO_NUMBER` is authorized to chat. Auto-reconnects with exponential backoff (1s → 60s cap). Works alongside Discord or standalone (no Discord token needed). `signal_bot.ws_loop()` started as `asyncio.create_task` from `on_ready()` or `run_without_discord()`.

## Bot Process Management

Use this exact local reboot sequence to avoid duplicate Discord replies:

```bash
# stop local + docker instances
pkill -9 -f "python3 main.py" 2>/dev/null; pkill -9 -f "Python main.py" 2>/dev/null; sleep 1
docker stop scout_report 2>/dev/null || true
docker stop context_bot 2>/dev/null || true  # legacy container name

# start local bot (override Docker-specific OLLAMA_URL from .env)
set -a && source .env && set +a && export OLLAMA_URL=http://localhost:11434 && .venv/bin/python3 main.py
```

Verify after start:

```bash
ps aux | grep "[p]ython.*main.py"
docker ps --format '{{.Names}}' | egrep '^(scout_report|context_bot)$' || true
```

Healthy startup logs must include:
- `Discord bot logged in as ...`
- `Scheduler started.`

## Rules

- Never log full calendar URLs (contain auth tokens). Use `_cal_labels`.
- `.env` is git-ignored with secrets. `.env.example` documents all vars.
- Syntax check all files: `for f in main.py scout_report/*.py; do python3 -c "import ast; ast.parse(open('$f').read())"; done`
- Build: `docker compose up -d --build`
- `google-genai` is imported lazily inside `_get_gemini_client()`, not at module top — missing package won't error until Gemini is actually used.
- Discord replies truncated at 1900 chars (2000 limit). Scheduled digests require `APPRISE_URL`.
- `HISTORY_DAYS=0` disables past-event classification entirely. Cache TTLs are in seconds (default 3600).
- At least one calendar URL must be configured or startup crashes.
- Timeouts are hard-coded: calendar fetch 30s, Ollama chat 120s, classification 15s.
