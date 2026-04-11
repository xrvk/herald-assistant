# Scout Report

A personal calendar assistant that aggregates events from iCloud, Outlook, Google Calendar (or any ICS feed), sends scheduled notification summaries via Discord (powered by [Apprise](https://github.com/caronc/apprise)), and lets you ask natural language questions about your schedule via a Discord bot backed by an LLM — either cloud via [Google Gemini](https://ai.google.dev/gemini-api/docs) or local via [Ollama](https://ollama.com/).

Runs as a Docker container with your choice of LLM backend.

## What It Does

| Feature | Description |
|---|---|
| **Weeknight digest** | Tomorrow's work events — configurable days/time (default Sun–Thu 8 PM) |
| **Weekend preview** | Fri–Sun events grouped by day — configurable day/time (default Thu 4 PM) |
| **Interactive chat** (Discord DM or channel) | Ask anything about your schedule — powered by Gemini or Ollama |

**Example questions you can ask the bot:**
- "Am I free Tuesday afternoon?"
- "What's on my calendar this weekend?"
- "Do I have any meetings before noon tomorrow?"
- "When is my next free day?"
- "What did I have yesterday?"
- "Recap last week's meetings"
- "How many meetings did I have last week?"

## Architecture

```
┌─────────────────────────────────────┐
│  Single Machine                     │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ Docker: scout-report          │  │
│  │ - cron scheduler              │  │
│  │ - discord bot                 │  │
│  │ - calendar fetcher            │  │
│  └───────────┬─────────┬─────────┘  │
│              │         │            │
│              ▼         ▼            │
│  ┌────────────┐   ┌──────────────┐  │
│  │Gemini API  │   │Ollama (local)│  │
│  │(cloud)     │   │gemma4:e4b    │  │
│  └────────────┘   └──────────────┘  │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ Docker: signal-cli-rest-api   │  │  ← optional (--profile signal)
│  │ (Signal notification sidecar) │  │
│  └───────────────────────────────┘  │
└─────────────┬───────────────────────┘
              │
              ▼
   Discord API / Signal
```

Set `LLM_BACKEND=gemini` (default) for Google's cloud API (fast, free tier, no GPU required), or `LLM_BACKEND=ollama` for local, privacy-preserving inference.

## Setup

See **[SETUP.md](SETUP.md)** for the full walkthrough — Discord bot creation, calendar URLs, Gemini API key, configuration, deployment (Docker or local), and troubleshooting.

Running on a NAS with the LLM on a separate machine? See **[NAS-DUAL-SETUP.md](NAS-DUAL-SETUP.md)**.

### Overview

1. **Get your calendar URLs** (iCloud, Outlook, Google, or any ICS feed)
2. **Create a Discord bot** and/or webhook for notifications
3. **Get a [Gemini API key](https://aistudio.google.com/app/apikey)** (free, no GPU needed) — or [install Ollama](https://ollama.com/) if you prefer local inference
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
| `LLM_BACKEND` | `gemini` | LLM provider: `gemini` (cloud) or `ollama` (local) |
| `GEMINI_API_KEY` | *(none)* | Google Gemini API key (required when `LLM_BACKEND=gemini`) |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model (when `LLM_BACKEND=gemini`) |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint (when `LLM_BACKEND=ollama`) |
| `OLLAMA_MODEL` | `gemma4:e4b` | Ollama model (when `LLM_BACKEND=ollama`) |
| `TZ` | `America/Los_Angeles` | Timezone ([IANA format](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) |
| `ICLOUD_LABEL` / `OUTLOOK_LABEL` / `GOOGLE_LABEL` | `Personal` / `Work` / `Google` | Calendar labels (shown in LLM context) |
| `WORK_LABELS` | `Work` | Which labels are work calendars (for digest + LLM) |
| `WEEKNIGHT_SCHEDULE` | `sun,mon,tue,wed,thu 20:00` | Weeknight digest schedule (or `off`) |
| `WEEKEND_SCHEDULE` | `thu 16:00` | Weekend preview schedule (or `off`) |
| `IGNORED_EVENTS` | *(none)* | Events to hide (comma-separated substrings) |
| `CONTEXT_DAYS` | `7` | Days ahead the LLM sees |
| `HISTORY_DAYS` | `10` | Days of past events available for history questions |
| `HISTORY_CACHE_TTL` | `21600` | Past events cache duration in seconds (default 6h) |
| `CACHE_TTL` | `3600` | Calendar cache duration in seconds |
| `SYSTEM_PROMPT` | *(built-in)* | Override the LLM system prompt |

> **Note on past events:** History questions (e.g. "what did I have yesterday?") are answered using past calendar data from your ICS feeds. The bot automatically detects when a question is about the past and includes historical context. Past event availability depends on your calendar provider — some providers prune past events from ICS feeds. The bot logs a warning at startup if a calendar returns no past events.

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

Any ICS/iCal feed works — iCloud, Outlook 365, Google Calendar, or custom URLs. See [SETUP.md](SETUP.md#1-get-calendar-urls) for instructions on getting each URL.

## Notification Targets

Scheduled digests are sent via [Apprise](https://github.com/caronc/apprise/wiki), which supports Discord webhooks, Telegram, Slack, email, Pushover, **Signal**, and [90+ other services](https://github.com/caronc/apprise/wiki). See [SETUP.md](SETUP.md#3-set-up-notifications-optional) for setup.

Signal notifications use the [signal-cli REST API](https://github.com/bbernhard/signal-cli-rest-api) sidecar container included in `docker-compose.yaml` (opt-in via `--profile signal`). Signal and Discord can be used at the same time — both targets receive every scheduled digest.

## License

[AGPL-3.0](LICENSE)
