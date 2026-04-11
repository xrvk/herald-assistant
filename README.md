# Scout Report

A personal calendar assistant that aggregates events from iCloud, Outlook, Google Calendar (or any ICS feed), sends scheduled notification summaries via Discord (powered by [Apprise](https://github.com/caronc/apprise)), and lets you ask natural language questions about your schedule via a Discord bot backed by an LLM — either cloud via [Google Gemini](https://ai.google.dev/gemini-api/docs) or local via [Ollama](https://ollama.com/).

Runs as a Docker container with your choice of LLM backend.

## What It Does

| Feature | Description |
|---|---|
| **Weeknight digest** | Tomorrow's work events — configurable days/time (default Sun–Thu 8 PM) |
| **Weekend preview** | Fri–Sun events grouped by day — configurable day/time (default Thu 4 PM) |
| **Interactive chat** (Discord DM or channel) | Ask anything about your schedule — powered by Gemini or Ollama |
| **Bot commands** | `!cal` (list calendars), `!llm` (show backend), `!switch g`/`o` (switch backend) |

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
└─────────────┬───────────────────────┘
              │
              ▼
         Discord API
```

Set `LLM_BACKEND=gemini` (default) for Google's cloud API (fast, free tier, no GPU required), or `LLM_BACKEND=ollama` for local, privacy-preserving inference.

## Setup

See **[SETUP.md](SETUP.md)** for the full walkthrough — Discord bot creation, calendar URLs, Gemini API key, configuration, deployment (Docker or local), and troubleshooting.

Running on a NAS with the LLM on a separate machine? See **[NAS-DUAL-SETUP.md](NAS-DUAL-SETUP.md)**.

### Overview

1. **Get your calendar URLs** (iCloud, Outlook, Google, or any ICS feed)
2. **Create a Discord bot** (for chat) and optionally a **webhook** (for scheduled digests)
3. **Get a [Gemini API key](https://aistudio.google.com/app/apikey)** (free, no GPU needed) — or [install Ollama](https://ollama.com/) if you prefer local inference
4. **`cp .env.example .env`** and fill in your values
5. **`docker compose up -d`** (or run locally with Python)

### Configuration

All config lives in a single `.env` file — copy [.env.example](.env.example) and fill in your values. See [SETUP.md](SETUP.md#5-configure) for step-by-step instructions.

**Required** (bot won't start without these):

| Variable | Description |
|---|---|
| `CALENDAR_N_URL` + `CALENDAR_N_LABEL` | Calendar ICS URLs (`N` = 1–9, at least one required). Legacy shortcuts also available — see .env.example |

**Conditional** (required depending on which features you enable):

| Variable | When required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Using Gemini (default setup) | Google Gemini API key — [get one free](https://aistudio.google.com/app/apikey) |
| `DISCORD_BOT_TOKEN` | Using interactive chat | Discord bot token (omit for notification-only mode) |
| `APPRISE_URL` | Sending scheduled digests | Notification URL — required when `WEEKNIGHT_SCHEDULE` or `WEEKEND_SCHEDULE` ≠ `off` |

**Optional** (sensible defaults built in):

| Variable | Default | Description |
|---|---|---|
| `LLM_BACKEND` | `gemini` | LLM provider: `gemini` (cloud) or `ollama` (local) |
| `DISCORD_CHANNEL_ID` | *(none)* | Channel for bot to listen in (omit for DM-only mode) |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model (when `LLM_BACKEND=gemini`) |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint (when `LLM_BACKEND=ollama`) |
| `OLLAMA_MODEL` | `gemma4:e4b` | Ollama model (when `LLM_BACKEND=ollama`) |
| `TZ` | `America/Los_Angeles` | Timezone ([IANA timezone format](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) |
| `WEEKNIGHT_SCHEDULE` | `off` | Weeknight digest schedule: `"days HH:MM"` or `off` |
| `WEEKEND_SCHEDULE` | `off` | Weekend preview schedule: `"days HH:MM"` or `off` |
| `ICLOUD_LABEL` / `OUTLOOK_LABEL` / `GOOGLE_LABEL` | `Personal` / `Work` / `Google` | Calendar labels (shown in LLM context) |
| `WORK_LABELS` | `Work` | Which labels are work calendars (for digest + LLM) |
| `IGNORED_EVENTS` | *(none)* | Events to hide (comma-separated substrings) |
| `CONTEXT_DAYS` | `7` | Days ahead the LLM sees |
| `HISTORY_DAYS` | `10` | Days of past events available for history questions |
| `HISTORY_CACHE_TTL` | `21600` | Past events cache duration in seconds (default 6h) |
| `CACHE_TTL` | `3600` | Calendar cache duration in seconds |
| `SYSTEM_PROMPT` | *(built-in)* | Override the LLM system prompt |
| `DISCORD_ALLOWED_USERS` | *(none — all users)* | Restrict bot to specific Discord user IDs (comma-separated) |
| `CONV_HISTORY_TURNS` | `3` | Q&A pairs kept per user for follow-up questions |
| `CONV_HISTORY_TTL` | `1800` | Conversation staleness timeout in seconds (30 min) |
| `CONV_HISTORY_CTX_BUMP` | `4096` | Extra Ollama context tokens when history overflows (no effect on Gemini) |

> **Note on past events:** History questions (e.g. "what did I have yesterday?") work by reading past events from your calendar feeds. Some calendar providers don't include past events in their feeds, which limits how far back the bot can look. The bot logs a warning at startup if a calendar returns no past events.

## Project Structure

```
├── main.py              # Scheduler + Discord bot + calendar fetch + LLM chat
├── requirements.txt     # Python dependencies
├── Dockerfile           # Container build definition
├── docker-compose.yaml  # Deployment config (reads .env)
├── .env                 # Your secrets (git-ignored)
├── .env.example         # Template for .env
├── scout_report/        # Python package (internal modules)
├── SETUP.md             # Full setup & deployment guide
├── NAS-DUAL-SETUP.md    # Dual-machine NAS + LLM host guide
└── .gitignore
```

## Calendar Sources

Any ICS/iCal feed works — iCloud, Outlook 365, Google Calendar, or custom URLs. Up to 9 numbered calendars (`CALENDAR_1` through `CALENDAR_9`) plus 3 legacy shortcuts — 12 total. See [SETUP.md](SETUP.md#1-get-calendar-urls) for instructions on getting each URL.

## Notification Targets

Scheduled digests are sent via [Apprise](https://github.com/caronc/apprise/wiki), which supports Discord webhooks, Telegram, Slack, email, Pushover, and [90+ other services](https://github.com/caronc/apprise/wiki). See [SETUP.md](SETUP.md#3-set-up-notifications-optional) for setup.

## License

[AGPL-3.0](LICENSE)
