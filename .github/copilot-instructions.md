# Scout Report

Single-file Python dual-mode bot (`main.py`, ~610 lines). ICS calendar aggregation + scheduled digests via Apprise. Optional Discord bot commands. Docker-deployed.

## Stack

Python 3.11 (Docker). Deps: discord.py, APScheduler, Apprise, icalendar, recurring-ical-events, requests. All config via env vars in `docker-compose.yaml`.

## Architecture

Dual-mode: `DISCORD_TOKEN` present → discord.Client bot + cron scheduler; absent → headless AsyncIOScheduler only. No LLM. No demo mode. No conversation history.

Entry point: `run()` — either `client.run()` (bot mode) or `asyncio.run(run_scheduler_only())` (headless).

## Bot Commands

Discord mode only. Smart-quote normalization via `_normalize_quotes()` for mobile keyboards.

| Command | Action |
|---------|--------|
| `.help` | Show available commands |
| `.cal` | List connected calendars |
| `.schedule` | View current digest schedules |
| `.schedule <type> <days> <HH:MM>` | Update a schedule (e.g. `.schedule weeknight mon,tue,wed 19:30`) |
| `.schedule <type> off` | Disable a schedule (e.g. `.schedule weekend off`) |

## Schedule Persistence

Env vars = defaults, `data/schedules.json` = overrides, `_rebuild_scheduler()` for hot-reload. `docker-compose.yaml` has `volumes: - ./data:/app/data` for persistence. `Dockerfile` has `RUN mkdir -p /app/data`.

## Scheduled Digests

Three digest types, all configurable via `"days HH:MM"` format or `"off"`:

| Digest | Env Var | Default | Description |
|--------|---------|---------|-------------|
| Weeknight | `WEEKNIGHT_SCHEDULE` | `off` | Tomorrow's work events (filtered by `WORK_LABELS`) |
| Noon brief | `NOON_SCHEDULE` | `off` | Tomorrow's work events — only fires if events exist |
| Weekend preview | `WEEKEND_SCHEDULE` | `off` | Fri–Sun events grouped by day |

At least one schedule must be enabled (notification-only bot).

## Startup Requirements

- Discord bot mode requires Message Content Intent enabled in Discord Developer Portal
- Startup sends notification to Apprise in both modes
- Healthy startup logs in bot mode: "Discord bot logged in as ..." + "Scheduler started."
- Healthy startup logs in headless: "Scout Report started. Scheduler running (headless mode)."

## Test Suite

- `tests/test_unit.py` — 40 unit tests, no network needed. Run: `pytest tests/test_unit.py -v`
- `run_tests.sh` — runner: `./run_tests.sh`

## Rules

- Never log full calendar URLs (contain auth tokens). Use `_cal_labels`.
- Syntax check: `python3 -c "import ast; ast.parse(open('main.py').read())"`
- Build: `docker compose up -d --build`
- At least one calendar URL must be configured or startup crashes.
- At least one schedule + `APPRISE_URL` must be configured or startup crashes.
- Startup prints a summary banner (calendars, schedules, timezone).
- Timeouts: calendar fetch 30s (with 1 retry + 2s backoff on transient errors).
- Cache TTL in seconds (default 3600).
- Graceful shutdown: `atexit` cleans up `_cal_executor`; `run_scheduler_only()` handles SIGTERM/SIGINT.
