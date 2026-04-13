# Scout Report

Single-file Python Discord bot (`main.py`). ICS calendar aggregation + LLM Q&A + scheduled digests. Docker-deployed.

## Stack

Python 3.11 (Docker) / 3.13 (local). Deps: discord.py, APScheduler, Apprise, icalendar, recurring-ical-events, google-genai. All config via env vars (no dotenv).

## LLM Backends

Ollama (local) or Gemini (cloud) via `LLM_BACKEND` env var. `get_backend()`/`set_backend()` for runtime state. `_get_gemini_client()` is lazy-init. Gemini free tier: ~5 RPM with retry/backoff.

## Conversation History

Per-user/channel in-memory history (`_conv_history` dict of deques). `_get_history()`/`_store_exchange()` helpers. Bot answers truncated to 500 chars before storage. TTL-based staleness (`CONV_HISTORY_TTL`, default 30 min). Ollama: `num_ctx` bumped by `CONV_HISTORY_CTX_BUMP` (default 4096) only when history overflows base window (avoids KV cache reload penalty); oldest exchanges dropped first if token budget exceeded. Gemini: history passed as `types.Content` list, no token concerns. `.llm <choice>` clears history. DMs keyed by `author.id` (not channel ID).

## Bot Process Management

Local reboot (single command — kill, wait, restart):

```bash
pkill -9 -f "python3 main.py" 2>/dev/null; pkill -9 -f "Python main.py" 2>/dev/null; sleep 0.5; cd /Users/victor/Projects/Personal\ Context && set -a && source .env && set +a && export OLLAMA_URL=http://localhost:11434 && .venv/bin/python3 main.py
```

Healthy startup logs must include:
- `Discord bot logged in as ...`
- `Scheduler started.`

## Bot Commands

All commands use `.` prefix. Smart-quote normalization for mobile keyboards.

| Command | Action |
|---------|--------|
| `.help` | Show available commands and example questions |
| `.llm` | Show current backend + models |
| `.llm [g\|o\|fl\|gf]` | Switch backend/model |
| `.cal` | List connected calendars |
| `.ignore` / `.infoevent` | Manage event filters. `add <pattern>` / `remove <pattern>` / `remove all` / (no args = list) |
| `.demo` / `.demo off` | Activate/deactivate synthetic demo calendars from `demo/calendars.py` |
| `.reboot` | Restart the bot process (`os.execv` self-restart; Docker `restart: always` as fallback) |

## Demo Mode

`.demo` injects synthetic calendars via `__demo_*` fake URLs stored directly in `_cal_cache`. `fetch_events()` has a guard: `url.startswith("__demo_")` returns cached data without HTTP fetch. Real calendars saved in `_demo_real_calendars`; `.demo off` restores them.

## Test Suite

- `tests/test_unit.py` — 145 unit tests, no bot/network needed. Run: `pytest tests/test_unit.py -v`
- `tests/test_integration.py` — live Discord integration tests (needs running bot + `.env`)
- `tests/demo_calendars.py` — synthetic calendar generators (`generate_work_ics()`, `generate_personal_ics()`, `calendar_stats()`)
- `run_tests.sh` — runner: `./run_tests.sh` (unit only), `./run_tests.sh --live` (unit + integration)

## Rules

- Never log full calendar URLs (contain auth tokens). Use `_cal_labels`.
- `.env` is git-ignored with secrets. `.env.example` documents all vars.
- Syntax check: `python3 -c "import ast; ast.parse(open('main.py').read())"`
- Build: `docker compose up -d --build`
- `google-genai` is imported lazily inside `_get_gemini_client()`, not at module top — missing package won't error until Gemini is actually used.
- Discord replies truncated at 1900 chars (2000 limit). Scheduled digests require `APPRISE_URL`.
- `HISTORY_DAYS=0` disables past-event classification entirely. Cache TTLs are in seconds (default 3600).
- At least one calendar URL must be configured or startup crashes.
- Startup prints a summary banner (LLM backend, calendars, schedules, history config).
- Timeouts are hard-coded: calendar fetch 30s (with 1 retry + 2s backoff on transient errors), Ollama chat 120s, classification 15s.
- `MAX_OUTPUT_TOKENS` configurable via env var (default 512).
- Event filters persisted in `filters.json`. `IGNORED_EVENTS`/`INFO_EVENTS` env vars are optional seeds merged at startup. Info events are visible to AI but tagged as informational.
- Graceful shutdown: `atexit` cleans up `_cal_executor`; `run_scheduler_only()` handles SIGTERM/SIGINT.
