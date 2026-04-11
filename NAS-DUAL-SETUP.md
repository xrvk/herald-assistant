# NAS Deployment Guide

Deployment guide for running the bot container 24/7 on a **Synology NAS**.

Two LLM options:

| | Gemini (Recommended) | Ollama (Optional) |
|---|---|---|
| **Setup** | Just an API key | Second machine on LAN |
| **Cost** | Free tier (~5 RPM) | Free, fully local |
| **Privacy** | Queries go to Google | Everything stays on your network |
| **Hardware** | NAS only | NAS + MacBook (or similar) |

---

## Architecture

### Option A: Gemini (Recommended)

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

Simplest option — the NAS handles everything. LLM queries go to Google's Gemini API over the internet. No second machine required.

### Option B: Ollama on MacBook

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

The bot calls Ollama over the LAN for LLM queries. If the MacBook is asleep or unreachable, the bot still runs — it just replies with a friendly offline message for chat queries.

In both options, scheduled notifications (weeknight/weekend) don't need the LLM — they only format calendar data and send via [Apprise](https://github.com/caronc/apprise) (requires `APPRISE_URL` in `.env`).

---

## 1. NAS: Deploy the Bot

### Prerequisites

- SSH enabled: **Control Panel → Terminal & SNMP → Enable SSH**
- Docker installed: **Package Center → Container Manager** (or Docker on older DSM)

### Copy Files to NAS

Via SMB share:

```bash
# From your Mac
cp -r "/path/to/scout-report" /Volumes/your-nas-share/docker/scout-report/
```

Or via SCP:

```bash
scp -r "/path/to/scout-report" admin@NAS_IP:/volume1/docker/scout-report/
```

### Configure .env

SSH into the NAS and edit `.env`:

```bash
ssh admin@NAS_IP
cd /volume1/docker/scout-report
cp .env.example .env
vi .env
```

#### Gemini (Recommended)

Get a free API key from [Google AI Studio](https://aistudio.google.com/app/apikey), then set:

```bash
LLM_BACKEND=gemini
GEMINI_API_KEY=your-api-key-here
# GEMINI_MODEL=gemini-2.5-flash   # default, can omit
```

That's it for LLM config — no `OLLAMA_URL` needed.

> **For Ollama on a MacBook instead**, see [Section 3](#3-optional-macbook-as-local-llm-ollama).

All other variables (calendar URLs, Discord token, etc.) are the same as [SETUP.md](SETUP.md#4-configure).

### Build and Start

```bash
cd /volume1/docker/scout-report
docker compose up -d
```

> **Note:** If `docker compose` isn't available, try `docker-compose` (older syntax) or use Synology's Container Manager UI to import the project.

### Check Logs

```bash
docker compose logs -f scout-report
```

You should see:

```
LLM backend: Gemini (gemini-2.5-flash)
Loaded 2 calendar(s): Personal, Work
  Work calendars: Work
  Personal calendars: Personal
Starting Discord bot + scheduler...
Discord bot logged in as YourBot#1234
  Weeknight digest: sun,mon,tue,wed,thu at 20:00
  Weekend preview: thu at 16:00
Scheduler started.
```

### Auto-Restart on Reboot

Already configured — `docker-compose.yaml` has `restart: always`.

---

## 2. Verify End-to-End

| Check | How |
|---|---|
| Bot is online | Look for it in your Discord server's member list |
| Bot responds | Send a message in the configured channel or DM the bot |
| LLM works | Ask "What's on my calendar tomorrow?" — should get a response within a few seconds |
| API key error | If misconfigured, bot replies with "🔑 Gemini API key is invalid or expired. Check your GEMINI\_API\_KEY." |
| Rate limiting | Free tier allows ~5 requests/min. If exceeded, bot replies with "⏳ Gemini rate limit reached — try again in a minute." |
| Scheduled notifications | Wait for the next scheduled time, or temporarily adjust `WEEKNIGHT_SCHEDULE`/`WEEKEND_SCHEDULE` in `.env` to test. Requires `APPRISE_URL` |

---

## 3. Optional: MacBook as Local LLM (Ollama)

Use this setup if you prefer a **fully local/private LLM** or don't want to use a cloud API. The bot runs on the NAS and calls Ollama on a MacBook (or any machine) over the LAN.

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

### Verify Ollama

From the NAS (or any machine on the LAN):

```bash
curl http://192.168.86.86:11434/api/tags
```

### Update .env on the NAS

Change the LLM config to point at the MacBook:

```bash
LLM_BACKEND=ollama
OLLAMA_URL=http://192.168.86.86:11434
OLLAMA_MODEL=gemma4:e4b
```

Then rebuild:

```bash
cd /volume1/docker/scout-report
docker compose up -d
```

Startup logs will show:

```
LLM backend: Ollama (gemma4:e4b at http://192.168.86.86:11434)
```

### Verify Ollama End-to-End

| Check | How |
|---|---|
| LLM works | Ask "What's on my calendar tomorrow?" — should respond in 5–15s |
| Ollama offline fallback | Stop Ollama on the Mac, send a message — bot should reply with "🔌 LLM is offline — Ollama may not be running or is unreachable." |
| Slow after Mac wake | First query after wake takes longer (model cold-loads ~15–20s). Subsequent queries are fast |

---

## Troubleshooting

### General

| Problem | Solution |
|---|---|
| `docker compose` not found on NAS | Use `docker-compose` (hyphenated) or install via Container Manager |
| Container exits immediately | Check `docker compose logs scout-report` — likely a missing env var |
| No scheduled notifications | Verify `APPRISE_URL` is set in `.env` |

### Gemini

| Problem | Solution |
|---|---|
| "🔑 Gemini API key is invalid or expired" | Check `GEMINI_API_KEY` in `.env` — get a key from [AI Studio](https://aistudio.google.com/app/apikey) |
| "⏳ Gemini rate limit reached" | Free tier allows ~5 RPM. Wait a moment or reduce query frequency |
| "❌ Something went wrong with Gemini" | Check `docker compose logs scout-report` for details |

### Ollama

| Problem | Solution |
|---|---|
| Bot can't reach Ollama | Verify MacBook IP, check `OLLAMA_HOST=0.0.0.0` is set, ensure no firewall blocking port 11434 |
| LLM always times out | MacBook may be sleeping — run `caffeinate -s`, check Energy settings |
| NAS can't resolve MacBook hostname | Use the IP address, not a hostname. Set a static IP or DHCP reservation |
| Bot responds slowly after Mac wake | First query after wake takes longer (model cold-loads ~15–20s). Subsequent queries are fast |

---

## Resource Usage

### NAS (Both Options)

| Component | RAM | CPU | Disk |
|---|---|---|---|
| scout-report container | ~60 MB | Minimal | ~200 MB image |

### MacBook (Ollama Only)

| Component | RAM | CPU | Disk |
|---|---|---|---|
| Ollama + gemma4:e4b | ~12 GB | Moderate during inference | ~10 GB model |

The NAS stays under 100 MB total for the bot. The MacBook only uses significant resources during active LLM queries.
