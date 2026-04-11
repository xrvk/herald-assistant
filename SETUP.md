# Setup Guide

Complete deployment instructions for Scout Report. The recommended approach is Docker — it handles dependencies, auto-restarts on reboot, and keeps your host clean. A local (non-Docker) option is also provided.

> **Fastest path:** Get a Gemini API key ([Step 4 Option A](#option-a-google-gemini-cloud--recommended)), set one calendar URL ([Step 1](#1-get-calendar-urls)), set `DISCORD_BOT_TOKEN` ([Step 2](#2-create-a-discord-bot)), and deploy ([Step 6](#6-deploy)). Scheduled digests are off by default in `.env.example`, so you don't need `APPRISE_URL` to get started.

---

## Prerequisites

- A machine to run the bot (Mac or Linux PC)
- [Docker and Docker Compose](https://docs.docker.com/get-started/get-docker/) (recommended) or [Python 3.11+](https://www.python.org/downloads/) (for running without Docker)
- A Discord server you control
- A free [Gemini API key](https://aistudio.google.com/app/apikey) (recommended) **or** [Ollama](https://ollama.com/) installed locally for private inference

---

## 1. Get Calendar URLs

Set at least one calendar. Multiple calendars from any provider can be used simultaneously.

### iCloud

1. Open **Calendar** on macOS or iCloud.com
2. Right-click a calendar → **Share Calendar** → check **Public Calendar**
3. Copy the `webcal://` URL

> **Tip:** You can paste the `webcal://` URL as-is — the bot automatically converts it to `https://`.

### Outlook 365

1. Go to [Outlook Web](https://outlook.office365.com/calendar)
2. Settings (gear) → **Calendar** → **Shared calendars**
3. Under **Publish a calendar**, select your calendar → **ICS** → **Publish**
4. Copy the ICS link

### Google Calendar

1. Go to [Google Calendar](https://calendar.google.com) → Settings (gear)
2. Click the calendar you want to add (left sidebar)
3. Scroll to **Integrate calendar**
4. Copy the **Secret address in iCal format** (not the public one, unless the calendar is public)

### Adding Calendars to `.env`

Use numbered slots — `CALENDAR_1_URL` + `CALENDAR_1_LABEL`, `CALENDAR_2_URL` + `CALENDAR_2_LABEL`, and so on. Any standard ICS/iCal URL works, from any provider, and you can add multiple calendars from the same service:

```env
CALENDAR_1_URL=https://calendar.google.com/calendar/ical/YOUR_ID/basic.ics
CALENDAR_1_LABEL=Personal
CALENDAR_2_URL=https://calendar.google.com/calendar/ical/ANOTHER_ID/basic.ics
CALENDAR_2_LABEL=Family
CALENDAR_3_URL=https://outlook.office365.com/owa/calendar/YOUR_ID/calendar.ics
CALENDAR_3_LABEL=Work
```

Up to 9 numbered calendars are supported (`CALENDAR_1` through `CALENDAR_9`).

Each calendar gets its own label, which the LLM uses to distinguish events by source. Labels are also used to decide which calendars count as "work" (see `WORK_LABELS`).

---

## 2. Create a Discord Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application** → name it → **Create**
2. **Bot** tab:
   - Click **Reset Token** → copy the token (you'll need this for `DISCORD_BOT_TOKEN`)
   - Enable **Message Content Intent** under Privileged Gateway Intents
   - Save Changes
3. **OAuth2** tab:
   - Under **Scopes**, check `bot`
   - Under **Bot Permissions**, check:
     - `Send Messages`
     - `Read Message History`
     - `View Channels`
   - Copy the generated URL → open it in your browser → add the bot to your server
4. **Get the channel ID:**
   - Discord → User Settings → Advanced → enable **Developer Mode**
   - Right-click the channel you want the bot to listen in → **Copy Channel ID**
5. **Get user IDs** (optional — for `DISCORD_ALLOWED_USERS`):
   - With Developer Mode on, right-click a user → **Copy User ID**

> **Important:** After adding the bot to your server, make sure it has permissions in your target channel. Right-click the channel → Edit Channel → Permissions → add the bot role → enable View Channel, Send Messages, and Read Message History.

---

## 3. Set Up Notifications (optional)

Scheduled digests (weeknight, weekend preview) are sent to a Discord channel via webhook. If you don't want scheduled digests, skip this step — set `WEEKNIGHT_SCHEDULE=off` and `WEEKEND_SCHEDULE=off` in `.env`.

### Discord Webhook

1. In your Discord server, go to the channel for notifications
2. Edit Channel → **Integrations** → **Webhooks** → **New Webhook**
3. Copy the webhook URL — it looks like `https://discord.com/api/webhooks/123456/abcdef`
4. Convert to Apprise format — replace the URL prefix:
   ```
   https://discord.com/api/webhooks/WEBHOOK_ID/WEBHOOK_TOKEN
   →  discord://WEBHOOK_ID/WEBHOOK_TOKEN
   ```
   For example, if your webhook URL is `https://discord.com/api/webhooks/1234567890/ABCdef`, your Apprise URL is `discord://1234567890/ABCdef`.

> **Tip:** Under the hood, notifications use [Apprise](https://github.com/caronc/apprise/wiki), so `APPRISE_URL` also accepts Telegram, Slack, and [90+ other services](https://github.com/caronc/apprise/wiki) if you ever want to switch.
>
> **Note:** The bot doesn't validate the Apprise URL at startup. To test, temporarily set a schedule to fire soon, or check logs when the first digest runs.

---

## 4. Set Up Your LLM Backend

Choose **one** of the two options below:

### Option A: Google Gemini (Cloud — Recommended)

No GPU, no local installs, no setup beyond an API key. The free tier is generous enough for personal use.

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey) and create an API key
2. Set `GEMINI_API_KEY` in your `.env` — that's it. `LLM_BACKEND=gemini` is already the default.

> **Free tier limits:** Gemini 2.5 Flash allows ~5 requests/minute on the free tier. For a personal calendar bot this is more than enough. The bot includes automatic retry with backoff for rate limits.
>
> **Privacy note:** Gemini sends your calendar data to Google's servers for processing. If privacy is a priority, use Ollama instead.

---

### Option B: Ollama (Local — Private, No Internet Required)

Ollama runs on the **same machine** as the bot container. The Docker container reaches it via `host.docker.internal` (macOS/Windows) or a host-gateway alias (Linux).

#### Install Ollama

**macOS:**

```bash
brew install ollama
```

**Linux:**

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

#### Choose a Gemma 4 Model

All models in the Gemma 4 family support text + image input, native system prompts, and configurable thinking modes. Pick based on your hardware:

| Model | Download | RAM Needed | Speed | Best For |
|---|---|---|---|---|
| `gemma4:e2b` | 7.2 GB | ~8 GB | Fastest | Low-RAM devices, quick answers |
| **`gemma4:e4b`** | **9.6 GB** | **~12 GB** | **Fast** | **Recommended — best speed/quality balance** |
| `gemma4:26b` | 18 GB | ~20 GB | Slow | Maximum accuracy, needs 24+ GB RAM |
| `gemma4:31b` | 20 GB | ~24 GB | Slowest | Dense model, research use |

> **Recommendation:** Start with **`gemma4:e4b`**. It's Google's edge-optimized model (4.5B effective parameters, 128K context) and responds in 5–15 seconds on an M2 Air. The `e2b` variant is viable on 8 GB machines but noticeably less capable. The `26b` MoE model is more accurate but takes 30–60 seconds per reply — too slow for chat.
>
> Other Ollama models work too (Llama, Mistral, etc.) — just set `OLLAMA_MODEL` to any model name. Gemma 4 is recommended for best results with this bot.

#### Pull the Model

```bash
ollama pull gemma4:e4b
```

#### Start Ollama

Ollama must listen on all interfaces (`0.0.0.0`) so the bot container can reach it:

```bash
OLLAMA_HOST=0.0.0.0 ollama serve
```

For better performance on Apple Silicon, enable flash attention and quantized KV cache:

```bash
OLLAMA_HOST=0.0.0.0 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve
```

**Run as a background service (macOS):**

```bash
# Set OLLAMA_HOST so it binds to all interfaces
launchctl setenv OLLAMA_HOST 0.0.0.0
brew services start ollama
```

**Run as a background service (Linux systemd):**

```bash
# Edit the systemd service
sudo systemctl edit ollama
# Add under [Service]:
#   Environment="OLLAMA_HOST=0.0.0.0"
sudo systemctl restart ollama
```

**Prevent Mac sleep (while plugged in):**

```bash
caffeinate -s &
```

> The bot gracefully handles the Ollama host being unreachable — it replies with a friendly offline message instead of crashing.

#### Verify Ollama Is Running

```bash
curl http://localhost:11434/api/tags
```

You should see a JSON response listing your model.

In `.env`, set `LLM_BACKEND=ollama` (overrides the `gemini` default).

---

## 5. Configure

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in the values you gathered from steps 1–4. The sections appear in the same order as the steps above — work through them top to bottom.

If you're using Gemini (the default), the only required LLM setting is `GEMINI_API_KEY`. If you're using Ollama, also set `LLM_BACKEND=ollama`.

Everything below the "Optional" divider has sensible defaults. This table covers optional tuning settings. For required and conditional variables, see the corresponding setup steps above or the summary in README.md.

Here's the full reference:

| Variable | Default | Description |
|---|---|---|
| `TZ` | `America/Los_Angeles` | Timezone ([IANA format](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) — controls digest timing and bot answers |
| `WEEKNIGHT_SCHEDULE` | `off` | Weeknight digest schedule: `"days HH:MM"` or `off` |
| `WEEKEND_SCHEDULE` | `off` | Weekend preview schedule: `"days HH:MM"` or `off` |
| `WORK_LABELS` | *(not set)* | Comma-separated calendar labels treated as work (must match your `CALENDAR_N_LABEL` values). If empty, weeknight digests will show "No meetings" |
| `IGNORED_EVENTS` | *(not set)* | Hide events from digests and LLM (comma-separated substrings, case-insensitive) |
| `CONTEXT_DAYS` | `7` | Days of future events the LLM can see |
| `HISTORY_DAYS` | `10` | Days of past events for history questions (`0` = off) |
| `HISTORY_CACHE_TTL` | `21600` | Past events cache in seconds (6h) |
| `CACHE_TTL` | `3600` | Calendar fetch cache in seconds (1h) |
| `CONV_HISTORY_TURNS` | `3` | Q&A pairs kept per user for follow-up questions |
| `CONV_HISTORY_TTL` | `1800` | Conversation staleness timeout in seconds (30 min) |
| `CONV_HISTORY_CTX_BUMP` | `4096` | Extra context tokens when history overflows (Ollama only — no effect on Gemini) |
| `SYSTEM_PROMPT` | *(built-in)* | Override the LLM personality (calendar data is appended automatically) |
| `DISCORD_ALLOWED_USERS` | *(not set)* | Security: bot only responds to these Discord user IDs (comma-separated). If unset, all users can interact |

> **Important:** Set `TZ` to your [IANA timezone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) (e.g., `Europe/London`, `Asia/Tokyo`). Default is `America/Los_Angeles`. This controls when scheduled digests fire and how times appear in the bot's answers.

> **Minimum to start:** At least one calendar URL, a Gemini API key (or `LLM_BACKEND=ollama`), and a Discord bot token. Scheduled digests are off by default in `.env.example`. If you enable them, you'll also need `APPRISE_URL`.
>
> **Minimum viable `.env`:**
> ```env
> CALENDAR_1_URL=https://your-calendar-url.ics
> CALENDAR_1_LABEL=Personal
> GEMINI_API_KEY=your_api_key_here
> DISCORD_BOT_TOKEN=your_token_here
> TZ=America/New_York
> ```
>
> The bot validates config at startup and will tell you what's missing.

> **Note:** Past event availability (`HISTORY_DAYS`) depends on your calendar provider — some prune old events from ICS feeds. The bot logs a warning at startup if a calendar returns no past events.

---

## 6. Deploy

### Option A: Docker (Recommended)

Requires Docker and Docker Compose on the host machine. If using Ollama, it must be running on the same machine — the container reaches it via `host.docker.internal:11434`.

> **Linux users:** The included `docker-compose.yaml` already maps `host.docker.internal` via `extra_hosts` — no extra setup needed.

**Build and start:**

```bash
docker compose up -d
```

**Check logs:**

```bash
docker compose logs -f scout-report
```

**Restart after code changes:**

```bash
docker compose up -d --build
```

**Stop:**

```bash
docker compose down
```

> The image is ~80 MB. Runtime memory usage is under 60 MB.

### Option B: Run Locally (no Docker)

Requires Python 3.11+.

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Load environment variables
set -a && source .env && set +a

# Start the bot
python main.py
```

To run in the background:

```bash
nohup python main.py > bot.log 2>&1 &
```

---

## 7. Verify Everything Works

1. **Check the bot is online:** It should appear as online in your Discord server
2. **Send a test message:** Type a question in the configured channel or DM the bot
3. **Try bot commands:** `!cal` lists connected calendars, `!llm` shows the active backend, `!switch g` / `!switch o` switches between Gemini and Ollama
4. **Expected response time:** 1–5 seconds with Gemini; 5–15 seconds with `gemma4:e4b` on Apple Silicon
5. **Check scheduled jobs:** Look for the schedule summary in the bot's startup logs

**Healthy startup logs look like:**

```
LLM backend: Gemini (gemini-2.5-flash)
Loaded 1 calendar(s): Personal
  Personal calendars: Personal
────────────────────────────────────────
  Discord bot: enabled (channel 123456789)
  Notifications: disabled (schedules off)
  History: 10 days back
────────────────────────────────────────
Starting Discord bot + scheduler...
Discord bot logged in as ScoutReport#1234
Scheduler started.
```

If the bot doesn't respond:
- Check logs: `docker compose logs -f scout-report`
- Verify the bot has channel permissions (View Channel, Send Messages, Read Message History)
- If using Gemini, confirm `GEMINI_API_KEY` is set and valid
- If using Ollama, verify Ollama is reachable: `curl http://OLLAMA_IP:11434/api/tags`
- Make sure only **one** bot instance is running

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Bot doesn't respond to messages | Check channel permissions — the bot needs View Channel, Send Messages, and Read Message History explicitly set |
| Bot starts but ignores messages | Verify `DISCORD_CHANNEL_ID` matches the actual channel (right-click channel → Copy Channel ID with Developer Mode enabled) |
| Gemini rate limit error | Free tier is ~5 RPM; the bot retries automatically. If persistent, check your API key quota at [Google AI Studio](https://aistudio.google.com/app/apikey) |
| LLM times out (Ollama) | Model may be cold-loading; try again in 30s. If persistent, switch to a smaller model (`gemma4:e2b`) |
| LLM offline message (Ollama) | Ollama isn't running, Mac is asleep, or `OLLAMA_URL` is wrong. Run `curl http://IP:11434` to test |
| Multiple replies to one message | Kill all bot processes and restart — only one instance should run at a time |
| Missing recurring events | Ensure `recurring-ical-events` is installed (included in `requirements.txt`) |
| Calendar URL not working | iCloud: must be a **public** calendar link. Outlook: must be the **ICS subscription** link. Google: use the **secret address in iCal format** |
| Docker build fails | Run `docker compose build --no-cache` to rebuild from scratch |
| Bot crashes on startup with "No calendar URLs configured" | Set at least one calendar: `CALENDAR_1_URL` in `.env`. See [Step 1](#1-get-calendar-urls) |

---

## Updating

```bash
git pull
docker compose up -d --build
```

Or without Docker:

```bash
git pull
pip install -r requirements.txt
# Restart the bot process
```
