# NAS Deployment Guide

Deployment guide for running the bot container 24/7 on a **Synology NAS** (DSM 7.x with Container Manager).

The recommended setup uses **Gemini** (Google's cloud LLM) — just an API key, no GPU, no second machine. If you prefer fully local/private inference, see [Appendix: Ollama on MacBook](#appendix-ollama-on-macbook-optional) at the bottom.

| | Gemini (Recommended) | Ollama (Optional) |
|---|---|---|
| **Setup** | Just an API key | Second machine on LAN |
| **Cost** | Free tier (~5 RPM) | Free, fully local |
| **Privacy** | Queries go to Google | Everything stays on your network |
| **Hardware** | NAS only | NAS + MacBook (or similar) |

---

## Architecture

```
┌──────────────────────────┐
│  Synology NAS            │
│  Docker: scout-report     │──HTTPS──> Gemini API (cloud)
│  - cron scheduler        │
│  - discord bot           │
│  - calendar fetcher      │
└──────────┬───────────────┘
           │
           ▼
      Discord API
```

The NAS handles everything. LLM queries go to Google's Gemini API over the internet. No second machine required.

Scheduled notifications (weeknight/weekend) don't need the LLM at all — they only format calendar data and send via [Apprise](https://github.com/caronc/apprise) (requires `APPRISE_URL` set in `docker-compose.yaml`).

---

## 1. Deploy the Bot

### Prerequisites

- **Container Manager** installed from **Package Center** (called "Docker" on DSM 6.x)
- Bot files copied to a shared folder on the NAS (e.g. `/docker/scout-report/`)

### Copy Files to NAS

Copy the project folder to your NAS via **File Station** (drag and drop), SMB share, or SCP:

```bash
# SMB (from your Mac, if the NAS share is mounted)
cp -r "/path/to/scout-report" /Volumes/your-nas-share/docker/scout-report/

# Or SCP
scp -r "/path/to/scout-report" admin@NAS_IP:/volume1/docker/scout-report/
```

### Configure docker-compose.yaml

The project ships a ready-made NAS configuration file — `docker-compose.nas.yaml` — with all settings inline. No separate `.env` file needed.

**Step 1 — Rename the NAS config file**

In **File Station**, navigate to the project folder, right-click `docker-compose.nas.yaml`, and rename it to `docker-compose.yaml`. (This replaces the development version — that's intentional. If you want to keep the original for reference, copy it first.)

> **Via SSH instead:** `mv docker-compose.nas.yaml docker-compose.yaml`

**Step 2 — Fill in your values**

Right-click `docker-compose.yaml` → **Open with Text Editor** and replace the placeholder strings with your actual values. The file is organised into labelled sections:

#### Calendars (at least one required)

Use numbered slots. Any ICS URL works — iCloud, Outlook, Google, etc. See [SETUP.md Step 1](SETUP.md#1-get-calendar-urls) for how to get these URLs.

```yaml
CALENDAR_1_URL: "webcal://p60-caldav.icloud.com/published/2/YOUR_ID"
CALENDAR_1_LABEL: "Personal"
# CALENDAR_2_URL: "https://outlook.office365.com/owa/calendar/YOUR_ID/calendar.ics"
# CALENDAR_2_LABEL: "Work"
```

Up to 9 numbered slots (`CALENDAR_1` through `CALENDAR_9`). Labels are your choice — they appear in digests, LLM context, and `WORK_LABELS` matching.

#### Discord Bot (required for interactive chat)

See [SETUP.md Step 2](SETUP.md#2-create-a-discord-bot) for creating the bot and getting these values.

```yaml
DISCORD_BOT_TOKEN: "your-discord-bot-token-here"
DISCORD_CHANNEL_ID: "your-channel-id-here"
```

Omit `DISCORD_CHANNEL_ID` for DM-only mode. Omit `DISCORD_BOT_TOKEN` entirely for notification-only mode (requires `APPRISE_URL` + a schedule below).

#### LLM Backend

```yaml
LLM_BACKEND: "gemini"
GEMINI_API_KEY: "your-gemini-api-key-here"
```

Get a free key from [Google AI Studio](https://aistudio.google.com/app/apikey). `LLM_BACKEND` already defaults to `gemini`, so you only need the API key.

#### Timezone

```yaml
TZ: "America/Los_Angeles"
```

Set this to your [IANA timezone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) (e.g. `Europe/London`, `Asia/Tokyo`). Controls when scheduled digests fire and how times appear in the bot's answers.

#### Notifications (optional)

Scheduled digests are off by default. To enable them, create a Discord webhook ([SETUP.md Step 3](SETUP.md#3-set-up-notifications-optional)), then uncomment and fill in:

```yaml
APPRISE_URL: "discord://webhook_id/webhook_token"
WEEKNIGHT_SCHEDULE: "sun,mon,tue,wed,thu 20:00"
WEEKEND_SCHEDULE: "thu 16:00"
WORK_LABELS: "Work"
```

`WORK_LABELS` must match your `CALENDAR_N_LABEL` values — it controls which calendars appear in the weeknight digest. If unset, weeknight digests show "No meetings."

#### Security: Restrict Bot Access

On a NAS that's always online, lock the bot to your Discord user ID by uncommenting:

```yaml
DISCORD_ALLOWED_USERS: "123456789012345678"
```

To find your user ID: Discord → Settings → Advanced → enable Developer Mode, then right-click your name → Copy User ID. Multiple IDs can be comma-separated.

#### Minimum required values

```yaml
CALENDAR_1_URL: "https://your-calendar-url.ics"
CALENDAR_1_LABEL: "Personal"
GEMINI_API_KEY: "your-gemini-api-key-here"
DISCORD_BOT_TOKEN: "your-discord-bot-token-here"
TZ: "America/New_York"
```

Everything else has sensible defaults. The bot validates config at startup and tells you what's missing.

For the full list of optional tuning settings see [SETUP.md §5](SETUP.md#5-configure).

### Build and Start (Container Manager)

1. Open **Container Manager → Project → Create**
2. **Project Name:** `scout-report`
3. **Path:** Select the folder where you copied the files (e.g. `/docker/scout-report`)
4. **Source:** Choose **Use existing docker-compose.yml** — Container Manager will detect the `docker-compose.yaml` in the folder
5. Click through the wizard and **Done**
6. When prompted, choose **Start the project**

Container Manager runs `docker compose up --build` behind the scenes — it builds the image from the `Dockerfile` and starts the container.

> **Updating after code or config changes:** Go to **Project → select scout-report → Action → Build**, then **Action → Start**. This rebuilds the image and restarts the container.

### Check Logs

**Container Manager → Project → scout-report → Details → Containers → select scout_report → Details → Log**

Or via SSH:

```bash
docker compose logs -f scout-report
```

You should see:

```
LLM backend: Gemini (gemini-2.5-flash)
Loaded 2 calendar(s): Personal, Work
  Personal calendars: Personal, Work
────────────────────────────────────────
  Discord bot: enabled (channel 123456789)
  Notifications: disabled (schedules off)
  History: 10 days back
────────────────────────────────────────
Starting Discord bot + scheduler...
Discord bot logged in as YourBot#1234
Scheduler started.
```

> If you've enabled scheduled digests (`WEEKNIGHT_SCHEDULE`/`WEEKEND_SCHEDULE` ≠ `off`), the log will also show the schedule times and "Notifications: enabled".

### Auto-Restart on Reboot

Already configured — `docker-compose.yaml` has `restart: always`. Container Manager will restart the container automatically after a NAS reboot or DSM update.

---

## 2. Verify End-to-End

| Check | How |
|---|---|
| Bot is online | Look for it in your Discord server's member list |
| Bot responds | Send a message in the configured channel or DM the bot |
| LLM works | Ask "What's on my calendar tomorrow?" — should get a response within a few seconds |
| API key error | If misconfigured, bot replies with "🔑 Gemini API key is invalid or expired. Check your GEMINI\_API\_KEY." |
| Rate limiting | Free tier allows ~5 requests/min. If exceeded, bot replies with "⏳ Gemini rate limit reached — try again in a minute." |
| Scheduled notifications | Wait for the next scheduled time, or temporarily adjust `WEEKNIGHT_SCHEDULE`/`WEEKEND_SCHEDULE` in `docker-compose.yaml` to test. Requires `APPRISE_URL` |

---

## 3. Troubleshooting

| Problem | Solution |
|---|---|
| Project won't build | Check **Project → Details → Containers → Log** for errors — likely a placeholder value still in `docker-compose.yaml` |
| Container exits immediately | Check the container log — common causes: missing `DISCORD_BOT_TOKEN`, no calendar URLs, invalid `GEMINI_API_KEY` |
| No scheduled notifications | Verify `APPRISE_URL` is set in `docker-compose.yaml` and schedules aren't `off` |
| "🔑 Gemini API key is invalid or expired" | Check `GEMINI_API_KEY` in `docker-compose.yaml` — get a key from [AI Studio](https://aistudio.google.com/app/apikey) |
| "⏳ Gemini rate limit reached" | Free tier allows ~5 RPM. Wait a moment or reduce query frequency |
| "❌ Something went wrong with Gemini" | Check `docker compose logs scout-report` for details |

---

## 4. Resource Usage

| Component | RAM | CPU | Disk |
|---|---|---|---|
| scout-report container | ~60 MB | Minimal | ~200 MB image |

The bot is I/O-bound (HTTP calls to calendar feeds and the Gemini API), not CPU-bound, so even entry-level NAS hardware works fine.

---
---

## Appendix: Ollama on MacBook (Optional)

Use this setup if you prefer a **fully local/private LLM** or don't want to use a cloud API. The bot runs on the NAS and calls Ollama on a MacBook (or any machine) over the LAN.

> **Local dev users:** If you're running the bot directly on your Mac (not on a NAS), you don't need any of the network config below. Set `LLM_BACKEND=ollama` in your `.env` and Ollama runs on the same machine. Add `GEMINI_API_KEY` to your `.env` and Gemini flash-lite is used automatically whenever Ollama is unreachable — no manual switching required.

### Architecture

```
┌──────────────────────────┐         ┌──────────────────────────┐
│  Synology NAS            │         │  MacBook (Apple Silicon)  │
│  Docker: scout-report     │──HTTP──>│  Ollama + gemma4:e4b     │
│  - cron scheduler        │         │  192.168.x.x:11434      │
│  - discord bot           │<────────│                          │
│  - calendar fetcher      │         └──────────────────────────┘
└──────────┬───────────────┘
           │
           ▼
      Discord API
```

If the MacBook is asleep or unreachable, the bot still runs — it just replies with a friendly offline message for chat queries. Scheduled notifications don't need the LLM at all.

### Install Ollama

```bash
brew install ollama
```

### Pull a Model

See the model comparison in [SETUP.md](SETUP.md#choose-a-gemma-4-model). For a MacBook M2 Air with 24 GB RAM, `gemma4:e4b` is recommended.

```bash
ollama pull gemma4:e4b
```

### Start Ollama on All Interfaces

The NAS needs to reach Ollama over the network, so bind to `0.0.0.0`:

```bash
OLLAMA_HOST=0.0.0.0 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 ollama serve
```

### Run as a Background Service

So Ollama survives terminal closes and login/logout:

```bash
launchctl setenv OLLAMA_HOST 0.0.0.0
brew services start ollama
```

### Prevent Sleep

The MacBook must stay awake for Ollama to be reachable. While plugged in:

```bash
caffeinate -s &
```

Or use **System Settings → Energy → Prevent automatic sleeping when the display is off**.

### Assign a Static IP (Recommended)

So `OLLAMA_URL` doesn't break when the MacBook gets a new DHCP lease:

1. **System Settings → Wi-Fi → Details (on your network) → TCP/IP**
2. Configure IPv4: **Manually**
3. Set IP Address (e.g. `192.168.86.86`), Subnet Mask (`255.255.255.0`), Router (your gateway)

Or reserve the IP in your router's DHCP settings.

### Verify Ollama is Reachable

From the NAS (or any machine on the LAN):

```bash
curl http://192.168.86.86:11434/api/tags
```

### Gemini Fallback (Optional)

If your MacBook is sometimes off or asleep, you can add a `GEMINI_API_KEY` alongside `LLM_BACKEND=ollama`. When Ollama is unreachable the bot automatically falls back to **Gemini flash-lite** instead of replying with an offline error.

In `docker-compose.yaml`, add your key to the LLM section:

```yaml
LLM_BACKEND: "ollama"
OLLAMA_URL: "http://192.168.86.86:11434"
OLLAMA_MODEL: "gemma4:e4b"
GEMINI_API_KEY: "your-gemini-api-key-here"   # ← enables auto-fallback
```

Get a free key from [Google AI Studio](https://aistudio.google.com/app/apikey). With the key set, startup logs will confirm the fallback is configured:

```
LLM backend: Ollama (gemma4:e4b at http://192.168.86.86:11434, Gemini fallback: gemini-2.5-flash-lite)
```

Ollama is always tried first. Gemini is only used when Ollama is unreachable. `!switch g` / `!switch o` still work to override at runtime.

### Update docker-compose.yaml on the NAS

Change the LLM config to point at the MacBook. In **File Station**, open `docker-compose.yaml` in Text Editor and update:

```yaml
LLM_BACKEND: "ollama"
# OLLAMA_URL: "http://192.168.86.86:11434"   ← uncomment and set your MacBook's IP
# OLLAMA_MODEL: "gemma4:e4b"                 ← uncomment
```

Then rebuild in Container Manager: **Project → scout-report → Action → Build**, then **Action → Start**.

Startup logs will show:

```
LLM backend: Ollama (gemma4:e4b at http://192.168.86.86:11434)
```

### Verify Ollama End-to-End

| Check | How |
|---|---|
| LLM works | Ask "What's on my calendar tomorrow?" — should respond in 5–15s |
| Ollama offline fallback (no Gemini key) | Stop Ollama on the Mac, send a message — bot should reply with "🔌 LLM is offline — Ollama may not be running or is unreachable." |
| Ollama offline fallback (with Gemini key) | Stop Ollama on the Mac, send a message — bot should automatically answer via Gemini flash-lite |
| Slow after Mac wake | First query after wake takes longer (model cold-loads ~15–20s). Subsequent queries are fast |

### Ollama Troubleshooting

| Problem | Solution |
|---|---|
| Bot can't reach Ollama | Verify MacBook IP, check `OLLAMA_HOST=0.0.0.0` is set, ensure no firewall blocking port 11434 |
| LLM always times out | MacBook may be sleeping — run `caffeinate -s`, check Energy settings |
| NAS can't resolve MacBook hostname | Use the IP address, not a hostname. Set a static IP or DHCP reservation |
| Bot responds slowly after Mac wake | First query after wake takes longer (model cold-loads ~15–20s). Subsequent queries are fast |

### Ollama Resource Usage

| Component | RAM | CPU | Disk |
|---|---|---|---|
| Ollama + gemma4:e4b | ~12 GB | Moderate during inference | ~10 GB model |

The MacBook only uses significant resources during active LLM queries. NAS resource usage stays the same (~60 MB).
