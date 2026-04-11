# Scout Report

A personal calendar assistant that aggregates events from iCloud, Outlook, Google Calendar (or any ICS feed), lets you ask natural language questions about your schedule via **Discord** and/or **Signal** interactive chat, and sends scheduled notification digests via [Apprise](https://github.com/caronc/apprise). Backed by an LLM — cloud via [Google Gemini](https://ai.google.dev/gemini-api/docs) or local via [Ollama](https://ollama.com/).

Runs as a Docker container with your choice of LLM backend and chat platform.

## What It Does

| Feature | Description |
|---|---|
| **Weeknight digest** | Tomorrow's work events — configurable days/time (default Sun–Thu 8 PM) |
| **Weekend preview** | Fri–Sun events grouped by day — configurable day/time (default Thu 4 PM) |
| **Interactive chat** (Discord DM/channel or Signal DM) | Ask anything about your schedule — powered by Gemini or Ollama |

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
┌─────────────────────────────────────────┐
│  Single Machine                         │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │ Docker: scout-report              │  │
│  │ - cron scheduler                  │  │
│  │ - discord bot (optional)          │  │
│  │ - calendar fetcher                │  │
│  └──────┬───────────┬────────────────┘  │
│         │           │                   │
│         ▼           ▼                   │
│  ┌────────────┐ ┌──────────────┐        │
│  │Gemini API  │ │Ollama (local)│        │
│  │(cloud)     │ │gemma4:e4b    │        │
│  └────────────┘ └──────────────┘        │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │ Docker: signal-cli-rest-api       │  │  ← optional (--profile signal)
│  │ (Signal chat + notifications)     │  │
│  └──────────┬────────────────────────┘  │
│             │ ws:// receive + REST send  │
└─────────┬───┴───────────────────────────┘
          │
          ▼
   Discord API / Signal
```

Set `LLM_BACKEND=gemini` (default) for Google's cloud API (fast, free tier, no GPU required), or `LLM_BACKEND=ollama` for local, privacy-preserving inference.

## Setup

See **[SETUP.md](SETUP.md)** for the full walkthrough — chat backends (Discord/Signal), calendar URLs, Gemini API key, configuration, deployment (Docker or local), and troubleshooting.

Running on a NAS with the LLM on a separate machine? See **[NAS-DUAL-SETUP.md](NAS-DUAL-SETUP.md)**.

### Overview

1. **Get your calendar URLs** (iCloud, Outlook, Google, or any ICS feed)
2. **Set up a chat backend** — Discord and/or Signal, including notifications
3. **Get a [Gemini API key](https://aistudio.google.com/app/apikey)** (free, no GPU needed) — or [install Ollama](https://ollama.com/) if you prefer local inference
4. **`cp .env.example .env`** and fill in your values
5. **`docker compose up -d`** (or run locally with Python)

### Configuration

All config lives in a single `.env` file. See [.env.example](.env.example) for the full reference with comments, or the [configuration section in SETUP.md](SETUP.md#4-configure) for details.

**Required** (set at least one calendar + at least one chat backend):

| Variable | Description |
|---|---|
| `ICLOUD_URL` / `OUTLOOK_URL` / `GOOGLE_URL` | Calendar ICS URLs (at least one) |
| `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID` | For interactive chat via Discord |
| `SIGNAL_CLI_REST_API_URL` + `SIGNAL_FROM_NUMBER` + `SIGNAL_TO_NUMBER` + `SIGNAL_CHAT=1` | For interactive chat via Signal DMs (requires signal-cli sidecar) |
| `APPRISE_URL` | Notification target for scheduled digests (optional) |

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
├── scout_report/         # Application package
│   ├── config.py         #   Configuration, env var parsing, constants
│   ├── calendar.py       #   Calendar fetching, caching, event parsing
│   ├── llm.py            #   LLM backends (Gemini/Ollama), classification
│   ├── notifications.py  #   Apprise setup, scheduled digests
│   ├── chat.py           #   Shared chat pipeline (platform-agnostic)
│   ├── discord_bot.py    #   Discord client
│   └── signal_bot.py     #   Signal WebSocket client
├── main.py               # Thin entry point
├── requirements.txt      # Python dependencies
├── Dockerfile            # Container build definition
├── docker-compose.yaml   # Deployment config (reads .env)
├── .env                  # Your secrets (git-ignored)
├── .env.example          # Template for .env
├── SETUP.md              # Full setup & deployment guide
├── NAS-DUAL-SETUP.md     # Dual-machine NAS + LLM host guide
└── .gitignore
```

## Calendar Sources

Any ICS/iCal feed works — iCloud, Outlook 365, Google Calendar, or custom URLs. See [SETUP.md](SETUP.md#1-get-calendar-urls) for instructions on getting each URL.

## Signal

Signal support has two modes, both powered by the [signal-cli REST API](https://github.com/bbernhard/signal-cli-rest-api) sidecar container (opt-in via `--profile signal` in `docker-compose.yaml`):

| Mode | What it does | How to enable |
|---|---|---|
| **Notifications** | Receives scheduled digest summaries | Set `SIGNAL_CLI_REST_API_URL`, `SIGNAL_FROM_NUMBER`, `SIGNAL_TO_NUMBER` |
| **Interactive chat** | Real-time DM Q&A (same as Discord chat) | Add `SIGNAL_CHAT=1` to the above |

Signal and Discord work independently or together — you can run chat on one and notifications on the other, or both on both. See [SETUP.md](SETUP.md#2b-signal-optional) for the full walkthrough.

## Notification Targets

Scheduled digests are sent via [Apprise](https://github.com/caronc/apprise/wiki), which supports Discord webhooks, Telegram, Slack, email, Pushover, Signal, and [90+ other services](https://github.com/caronc/apprise/wiki). See [SETUP.md](SETUP.md#2c-other-notification-services-optional) for setup.

## License

[AGPL-3.0](LICENSE)
