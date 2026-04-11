# Setup Guide

Complete deployment instructions for Scout Report. The recommended approach is Docker — it handles dependencies, auto-restarts on reboot, and keeps your host clean. A local (non-Docker) option is also provided.

> **Fastest path:** If you just want to try it out, use Gemini (no installs needed — just an API key). See [Step 4 Option B](#option-b-google-gemini-cloud--no-gpu-required).

---

## Prerequisites

- A machine to run the bot (Mac or Linux PC)
- [Docker and Docker Compose](https://docs.docker.com/get-started/get-docker/) (recommended) or Python 3.10+
- A Discord server you control
- Either [Ollama](https://ollama.com/) installed locally **or** a free [Gemini API key](https://aistudio.google.com/app/apikey)

---

## 1. Get Calendar URLs

Set at least one calendar. All three (plus extras) can be used simultaneously.

### iCloud

1. Open **Calendar** on macOS or iCloud.com
2. Right-click a calendar → **Share Calendar** → check **Public Calendar**
3. Copy the `webcal://` URL

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

### Other ICS Feeds

Any standard ICS/iCal URL works. Set `CALENDAR_1_URL` and `CALENDAR_1_LABEL` in `.env` (up to 9 extra calendars).

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

> **Important:** After adding the bot to your server, make sure it has permissions in your target channel. Right-click the channel → Edit Channel → Permissions → add the bot role → enable View Channel, Send Messages, and Read Message History.

---

## 3. Set Up Notifications (optional)

Scheduled digests (weeknight, weekend preview) are sent via [Apprise](https://github.com/caronc/apprise/wiki), which supports 90+ notification services. If you don't want scheduled digests, skip this step — set `WEEKNIGHT_SCHEDULE=off` and `WEEKEND_SCHEDULE=off` in `.env`.

### Discord Webhook

1. In your Discord server, go to the channel for notifications
2. Edit Channel → **Integrations** → **Webhooks** → **New Webhook**
3. Copy the webhook URL — it looks like `https://discord.com/api/webhooks/123456/abcdef`
4. Convert to Apprise format: `discord://123456/abcdef` (replace the `https://discord.com/api/webhooks/` prefix with `discord://`)

### Other Services

Apprise supports Telegram, Slack, email, Pushover, and [many more](https://github.com/caronc/apprise/wiki). See the wiki for URL formats.

---

## 4. Set Up Your LLM Backend

Choose **one** of the two options below:

### Option A: Ollama (Local — Private, No Internet Required)

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

### Option B: Google Gemini (Cloud — No GPU Required)

If you don't want to run a local LLM, you can use Google's Gemini API instead. It's fast, free-tier friendly, and requires no GPU.

1. Go to [Google AI Studio](https://aistudio.google.com/app/apikey) and create an API key
2. Save it — you'll set `LLM_BACKEND=gemini` and `GEMINI_API_KEY` in the next step

> **Free tier limits:** Gemini 2.5 Flash allows ~5 requests/minute on the free tier. For a personal calendar bot this is more than enough. The bot includes automatic retry with backoff for rate limits.
>
> **Privacy note:** Unlike Ollama, Gemini sends your calendar data to Google's servers for processing. If privacy is a priority, use Ollama instead.

---

## 5. Configure

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in the values you gathered from steps 1–4. The sections appear in the same order as the steps above — work through them top to bottom.

Everything below the "Optional" divider has sensible defaults. Each variable has an inline comment explaining what it does.

> **Note:** Past event availability (`HISTORY_DAYS`) depends on your calendar provider — some prune old events from ICS feeds. The bot logs a warning at startup if a calendar returns no past events.

---

## 6. Deploy

### Option A: Docker (Recommended)

Requires Docker and Docker Compose on the host machine. If using Ollama, it must be running on the same machine — the container reaches it via `host.docker.internal:11434`.

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

Requires Python 3.10+.

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Load environment variables
export $(grep -v '^#' .env | xargs)

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
3. **Expected response time:** 5–15 seconds with `gemma4:e4b` on Apple Silicon
4. **Check scheduled jobs:** Look for the schedule summary in the bot's startup logs

If the bot doesn't respond:
- Check logs: `docker compose logs -f scout-report`
- Verify the bot has channel permissions (View Channel, Send Messages, Read Message History)
- Verify Ollama is reachable: `curl http://OLLAMA_IP:11434/api/tags`
- Make sure only **one** bot instance is running

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Bot doesn't respond to messages | Check channel permissions — the bot needs View Channel, Send Messages, and Read Message History explicitly set |
| LLM times out | Model may be cold-loading; try again in 30s. If persistent, switch to a smaller model (`gemma4:e2b`) |
| LLM offline message | Ollama isn't running, Mac is asleep, or `OLLAMA_URL` is wrong. Run `curl http://IP:11434` to test |
| Multiple replies to one message | Kill all bot processes and restart — only one instance should run at a time |
| Missing recurring events | Ensure `recurring-ical-events` is installed (included in `requirements.txt`) |
| Calendar URL not working | iCloud: must be a **public** calendar link. Outlook: must be the **ICS subscription** link. Google: use the **secret address in iCal format** |
| Docker build fails | Run `docker compose build --no-cache` to rebuild from scratch |

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
