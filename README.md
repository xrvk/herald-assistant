# Scout Report

A personal calendar assistant that aggregates events from iCloud, Outlook, Google Calendar (or any ICS feed), sends scheduled notification summaries via Discord (powered by [Apprise](https://github.com/caronc/apprise)), and lets you ask natural language questions about your schedule via a Discord bot backed by an LLM — either cloud via [Google Gemini](https://ai.google.dev/gemini-api/docs) or local via [Ollama](https://ollama.com/).

Runs as a Docker container with your choice of LLM backend.

## What It Does

| Feature | Description |
|---|---|
| **Weeknight digest** | Tomorrow's work events — configurable days/time (off by default, see SETUP.md to enable) |
| **Weekend preview** | Fri–Sun events grouped by day — configurable day/time (off by default, see SETUP.md to enable) |
| **Interactive chat** (Discord DM or channel) | Ask anything about your schedule — powered by Gemini or Ollama |
| **Bot commands** | `.cal` (list calendars), `.llm` (show backend), `.llm g`/`o` (switch backend) |

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
| `CALENDAR_N_URL` + `CALENDAR_N_LABEL` | Calendar ICS URLs (`N` = 1–9, at least one required) |

**Conditional** (required depending on which features you enable):

| Variable | When required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Using Gemini (default setup) | Google Gemini API key — [get one free](https://aistudio.google.com/app/apikey) |
| `GEMINI_MODEL` | Using Gemini (default setup) | Gemini model (default `gemini-2.5-flash`) |
| `DISCORD_BOT_TOKEN` | Using interactive chat | Discord bot token (omit for notification-only mode) |
| `APPRISE_URL` | Sending scheduled digests | Notification URL — required when `WEEKNIGHT_SCHEDULE` or `WEEKEND_SCHEDULE` ≠ `off` |

**Optional** (sensible defaults built in):

| Variable | Default | Description |
|---|---|---|
| `LLM_BACKEND` | `gemini` | LLM provider: `gemini` (cloud) or `ollama` (local) |
| `DISCORD_CHANNEL_ID` | *(none)* | Channel for bot to listen in (omit for DM-only mode) |
| `OLLAMA_URL` | `http://host.docker.internal:11434` | Ollama endpoint (when `LLM_BACKEND=ollama`) |
| `OLLAMA_MODEL` | `gemma4:e4b` | Ollama model (when `LLM_BACKEND=ollama`) |
| `TZ` | `America/Los_Angeles` | Timezone ([IANA timezone format](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) |
| `WEEKNIGHT_SCHEDULE` | `off` | Weeknight digest schedule: `"days HH:MM"` or `off` |
| `WEEKEND_SCHEDULE` | `off` | Weekend preview schedule: `"days HH:MM"` or `off` |
| `DISCORD_ALLOWED_USERS` | *(not set)* | Security: bot will only respond to these Discord user IDs (comma-separated). If unset, all users can interact |
| `IGNORED_EVENTS` | *(not set)* | Hide events from digests and LLM (comma-separated substrings, case-insensitive). Quotes and special chars stripped for fuzzy matching |
| `NON_BLOCKING_EVENTS` | *(not set)* | Events visible to LLM but don't block your availability (same format as `IGNORED_EVENTS`) |

See [SETUP.md](SETUP.md#5-configure) and [.env.example](.env.example) for the full list of tuning options (calendar labels, event filtering, history settings, conversation memory, system prompt override, etc.).

> **Note on past events:** History questions (e.g. "what did I have yesterday?") work by reading past events from your calendar feeds. Some calendar providers don't include past events in their feeds, which limits how far back the bot can look. The bot logs a warning at startup if a calendar returns no past events.

## Project Structure

```
├── main.py              # Scheduler + Discord bot + calendar fetch + LLM chat
├── requirements.txt     # Python dependencies
├── Dockerfile           # Container build definition
├── docker-compose.yaml  # Deployment config (reads .env)
├── .env                 # Your secrets (git-ignored)
├── .env.example         # Template for .env
├── scout_report/        # Python package (reserved for future modules)
├── SETUP.md             # Full setup & deployment guide
├── NAS-DUAL-SETUP.md    # Dual-machine NAS + LLM host guide
└── .gitignore
```

## Calendar Sources

Any ICS/iCal feed works — iCloud, Outlook 365, Google Calendar, or custom URLs. Up to 9 numbered calendars (`CALENDAR_1` through `CALENDAR_9`). See [SETUP.md](SETUP.md#1-get-calendar-urls) for instructions on getting each URL.

## Notification Targets

Scheduled digests are sent via [Apprise](https://github.com/caronc/apprise/wiki), which supports Discord webhooks, Telegram, Slack, email, Pushover, and [90+ other services](https://github.com/caronc/apprise/wiki). See [SETUP.md](SETUP.md#3-set-up-notifications-optional) for setup.

## License

[AGPL-3.0](LICENSE)
