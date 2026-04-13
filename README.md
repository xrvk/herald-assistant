# Scout Report

A dual-mode calendar bot that aggregates events from iCloud, Outlook, Google Calendar (or any ICS feed) and sends scheduled digest notifications via [Apprise](https://github.com/caronc/apprise) (Discord webhooks, Telegram, Slack, and [90+ other services](https://github.com/caronc/apprise/wiki)).

Runs in two modes:

- **Discord bot mode** (if `DISCORD_TOKEN` is set): Discord bot with `.help`, `.cal`, `.schedule` commands + scheduled digests
- **Headless mode** (no `DISCORD_TOKEN`): Scheduler-only with cron-based digests

Runs as a lightweight Docker container — designed for always-on deployment on a Synology NAS.

## What It Does

| Digest | Description |
|---|---|
| **Weeknight** | Tomorrow's work events — fires Sun–Thu at your configured time |
| **Noon brief** | Tomorrow's work events at midday — only fires if events exist |
| **Weekend preview** | Fri–Sun events grouped by day — fires Thursday afternoon |

All schedules are configurable (`"days HH:MM"` format) or can be disabled (`"off"`).

## Architecture

**Discord bot mode:**
```
┌──────────────────────────┐
│  Docker: scout-report    │
│  - Discord bot           │──HTTPS──> Discord API
│  - APScheduler (cron)    │
│  - calendar fetcher      │──HTTPS──> ICS calendar feeds
│  - Apprise notifier      │──HTTPS──> Discord webhook
└──────────────────────────┘
```

**Headless mode:**
```
┌──────────────────────────┐
│  Docker: scout-report    │
│  - APScheduler (cron)    │
│  - calendar fetcher      │──HTTPS──> ICS calendar feeds
│  - Apprise notifier      │──HTTPS──> Discord webhook
└──────────────────────────┘
```

No LLM. Just calendar data → cron → webhook notifications (+ optional Discord bot commands).

## Quick Start

1. **Get your calendar URLs** (iCloud, Outlook, Google, or any ICS feed)
2. **Create a Discord webhook** (or configure another [Apprise target](https://github.com/caronc/apprise/wiki))
3. **Edit `docker-compose.yaml`** — fill in calendar URLs, timezone, Apprise URL, and schedules
4. **`docker compose up -d --build`**

See **[NAS-DUAL-SETUP.md](NAS-DUAL-SETUP.md)** for the full Synology NAS walkthrough (DS416play tested).

## Configuration

All config lives in `docker-compose.yaml` — no separate `.env` file needed.

**Required:**

| Variable | Description |
|---|---|
| `CALENDAR_N_URL` + `CALENDAR_N_LABEL` | Calendar ICS URLs (`N` = 1–9, at least one required) |
| `APPRISE_URL` | Notification URL (e.g. `discord://webhook_id/webhook_token`) |

**Optional:**

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Discord bot token (enables bot mode with `.help`, `.cal`, `.schedule` commands) |

**At least one schedule must be enabled:**

| Variable | Default | Description |
|---|---|---|
| `WEEKNIGHT_SCHEDULE` | `off` | `"days HH:MM"` — e.g. `"sun,mon,tue,wed,thu 20:00"` |
| `NOON_SCHEDULE` | `off` | `"days HH:MM"` — noon brief (only fires if events exist) |
| `WEEKEND_SCHEDULE` | `off` | `"days HH:MM"` — e.g. `"thu 16:00"` |

**Advanced:**

| Variable | Default | Description |
|---|---|---|
| `TZ` | `America/Los_Angeles` | [IANA timezone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) — controls digest timing |
| `WORK_LABELS` | *(empty)* | Comma-separated calendar labels that count as "work" (used by weeknight + noon digests) |
| `CACHE_TTL` | `3600` | Calendar fetch cache in seconds |

## Calendar Sources

Any ICS/iCal feed works — iCloud, Outlook 365, Google Calendar, or custom URLs. Up to 9 numbered calendars (`CALENDAR_1` through `CALENDAR_9`). See [NAS-DUAL-SETUP.md](NAS-DUAL-SETUP.md#1-get-calendar-urls) for instructions on getting each URL.

## Notification Targets

Digests are sent via [Apprise](https://github.com/caronc/apprise/wiki), which supports Discord webhooks, Telegram, Slack, email, Pushover, and [90+ other services](https://github.com/caronc/apprise/wiki).

## Bot Commands

When `DISCORD_TOKEN` is set, the bot provides these commands:

| Command | Description |
|---|---|
| `.help` | Show available commands |
| `.cal` | List connected calendars |
| `.schedule` | View current digest schedules |
| `.schedule <type> <days> <HH:MM>` | Update a schedule (e.g. `.schedule weeknight mon,tue,wed 19:30`) |
| `.schedule <type> off` | Disable a schedule (e.g. `.schedule weekend off`) |

Schedule types: `weeknight`, `noon`, `weekend`. Schedule overrides are persisted to `data/schedules.json` and survive container restarts.

**Requirements for Discord bot mode:**
- Create a Discord bot and get the token from [Discord Developer Portal](https://discord.com/developers/applications)
- Enable **Message Content Intent** in the bot settings
- Add the bot to your server with appropriate permissions

## Project Structure

```
├── main.py              # Dual-mode bot + scheduler + calendar fetch + notification pipeline
├── requirements.txt     # Python dependencies (includes discord.py)
├── Dockerfile           # Container build definition
├── docker-compose.yaml  # Deployment config (all settings inline)
├── NAS-DUAL-SETUP.md    # Full NAS deployment guide
├── data/                # Persistent data directory
│   └── schedules.json   # Schedule overrides (auto-created)
├── tests/               # Unit tests
└── .gitignore
```

## Tests

```bash
pytest tests/test_unit.py -v
```

## License

[AGPL-3.0](LICENSE)
