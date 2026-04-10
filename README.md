# Personal Context Bot

A personal calendar assistant that aggregates events from iCloud, Outlook, Google Calendar (or any ICS feed), sends scheduled notification summaries via Discord (powered by [Apprise](https://github.com/caronc/apprise)), and lets you ask natural language questions about your schedule via a Discord bot backed by a local LLM ([Ollama](https://ollama.com/) + [Gemma 4](https://ai.google.dev/gemma/docs/core)).

Runs as a Docker container alongside Ollama on a single machine.

## What It Does

| Feature | Description |
|---|---|
| **Weeknight digest** | Tomorrow's work events — configurable days/time (default Sun–Thu 8 PM) |
| **Weekend preview** | Fri–Sun events grouped by day — configurable day/time (default Thu 4 PM) |
| **Interactive chat** (Discord DM or channel) | Ask anything about your schedule — powered by Gemma 4 via Ollama |

**Example questions you can ask the bot:**
- "Am I free Tuesday afternoon?"
- "What's on my calendar this weekend?"
- "Do I have any meetings before noon tomorrow?"
- "When is my next free day?"

## Architecture

```
┌─────────────────────────────────────┐
│  Single Machine                     │
│                                     │
│  ┌───────────────────────────────┐  │
│  │  Docker: context-bot           │  │
│  │  - cron scheduler              │  │
│  │  - discord bot                 │  │
│  │  - calendar fetcher            │  │
│  └───────────────┬───────────────┘  │
│                │                    │
│                ▼                    │
│  ┌───────────────────────────────┐  │
│  │  Ollama + gemma4:e4b           │  │
│  │  (host.docker.internal:11434) │  │
│  └───────────────────────────────┘  │
└─────────────────┬───────────────────┘
                  │
                  ▼
             Discord API
```

## Setup

See **[SETUP.md](SETUP.md)** for the full walkthrough — Ollama install, Discord bot creation, calendar URLs, configuration, deployment (Docker or local), and troubleshooting.

Running on a NAS with the LLM on a separate machine? See **[NAS-DUAL-SETUP.md](NAS-DUAL-SETUP.md)**.

### Overview

1. **Install Ollama** and pull a model (runs on the host for GPU access)
2. **Create a Discord bot** and/or webhook for notifications
3. **Get your calendar URLs** (iCloud, Outlook, Google, or any ICS feed)
4. **`cp .env.example .env`** and fill in your values
5. **`docker compose up -d`** (or run locally with Python)

### Configuration

All config lives in a single `.env` file. See [.env.example](.env.example) for the full reference with comments, or the [configuration section in SETUP.md](SETUP.md#5-configure) for details.

**Required** (set at least one calendar + one of Discord bot or notifications):

| Variable | Description |
|---|---|
| `ICLOUD_URL` / `OUTLOOK_URL` / `GOOGLE_URL` | Calendar ICS URLs (at least one) |
| `APPRISE_URL` | Notification target — required if scheduled digests are enabled |
| `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID` | For interactive LLM chat via Discord |

**Optional** (sensible defaults built in):

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | `gemma4:e4b` | Ollama model to use |
| `TZ` | `America/Los_Angeles` | Timezone ([IANA format](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) |
| `ICLOUD_LABEL` / `OUTLOOK_LABEL` / `GOOGLE_LABEL` | `Personal` / `Work` / `Google` | Calendar labels (shown in LLM context) |
| `WORK_LABELS` | `Work` | Which labels are work calendars (for digest + LLM) |
| `WEEKNIGHT_SCHEDULE` | `sun,mon,tue,wed,thu 20:00` | Weeknight digest schedule (or `off`) |
| `WEEKEND_SCHEDULE` | `thu 16:00` | Weekend preview schedule (or `off`) |
| `IGNORED_EVENTS` | *(none)* | Events to hide (comma-separated substrings) |
| `CONTEXT_DAYS` | `7` | Days ahead the LLM sees |
| `CACHE_TTL` | `3600` | Calendar cache duration in seconds |
| `SYSTEM_PROMPT` | *(built-in)* | Override the LLM system prompt |

## Project Structure

```
├── main.py              # Scheduler + Discord bot + calendar fetch + LLM chat
├── requirements.txt     # Python dependencies
├── Dockerfile           # Container build definition
├── docker-compose.yaml  # Deployment config (reads .env)
├── .env                 # Your secrets (git-ignored)
├── .env.example         # Template for .env
├── SETUP.md             # Full setup & deployment guide
├── NAS-DUAL-SETUP.md    # Dual-machine NAS + LLM host guide
└── .gitignore
```

## Calendar Sources

Any ICS/iCal feed works — iCloud, Outlook 365, Google Calendar, or custom URLs. See [SETUP.md](SETUP.md#3-get-calendar-urls) for instructions on getting each URL.

## Notification Targets

Scheduled digests are sent via [Apprise](https://github.com/caronc/apprise/wiki), which supports Discord webhooks, Telegram, Slack, email, Pushover, and [90+ other services](https://github.com/caronc/apprise/wiki). See [SETUP.md](SETUP.md#4-set-up-notifications-optional) for setup.

## License

[AGPL-3.0](LICENSE)
