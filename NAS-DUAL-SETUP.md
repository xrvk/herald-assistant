# NAS + MacBook Dual-Machine Setup

Deployment guide for running the bot container on a **Synology NAS** with the LLM (Ollama) on a **MacBook** on the same local network.

This is the recommended setup when your NAS doesn't have the CPU/RAM to run an LLM locally.

---

## Architecture

```
┌──────────────────────────┐         ┌──────────────────────────┐
│  Synology NAS            │         │  MacBook (Apple Silicon)  │
│  Docker: context-bot     │──HTTP──>│  Ollama + gemma4:e4b     │
│  - cron scheduler        │         │  192.168.x.x:11434      │
│  - discord bot           │<────────│                          │
│  - calendar fetcher      │         └──────────────────────────┘
└──────────┬───────────────┘
           │
           ▼
      Discord API
```

The bot container runs 24/7 on the NAS (~60 MB RAM). It calls Ollama over the LAN for LLM queries. If the MacBook is asleep or unreachable, the bot still runs — it just replies with a friendly offline message for chat queries. Scheduled notifications (weeknight/weekend) don't need the LLM.

---

## 1. MacBook: Set Up Ollama

### Install

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

### Verify

From the NAS (or any machine on the LAN):

```bash
curl http://192.168.86.86:11434/api/tags
```

---

## 2. NAS: Deploy the Bot

### Prerequisites

- SSH enabled: **Control Panel → Terminal & SNMP → Enable SSH**
- Docker installed: **Package Center → Container Manager** (or Docker on older DSM)

### Copy Files to NAS

Via SMB share:

```bash
# From your Mac
cp -r "/path/to/Personal Context" /Volumes/your-nas-share/docker/context-bot/
```

Or via SCP:

```bash
scp -r "/path/to/Personal Context" admin@NAS_IP:/volume1/docker/context-bot/
```

### Configure .env

SSH into the NAS and edit `.env`:

```bash
ssh admin@NAS_IP
cd /volume1/docker/context-bot
cp .env.example .env
vi .env
```

Key difference from single-machine setup — use the **MacBook's LAN IP** for Ollama:

```bash
# Point to MacBook on the LAN (NOT host.docker.internal)
OLLAMA_URL=http://192.168.86.86:11434
OLLAMA_MODEL=gemma4:e4b
```

All other variables (calendar URLs, Discord token, etc.) are the same as [SETUP.md](SETUP.md#5-configure-environment).

### Build and Start

```bash
cd /volume1/docker/context-bot
docker compose up -d
```

> **Note:** If `docker compose` isn't available, try `docker-compose` (older syntax) or use Synology's Container Manager UI to import the project.

### Check Logs

```bash
docker compose logs -f context-bot
```

You should see:

```
Loaded 2 calendar(s): Personal, Work
Starting Discord bot + scheduler...
Discord bot logged in as Friday#4751
Scheduler started.
```

### Auto-Restart on Reboot

Already configured — `docker-compose.yaml` has `restart: always`.

---

## 3. Verify End-to-End

| Check | How |
|---|---|
| Bot is online | Look for it in your Discord server's member list |
| Bot responds | Send a message in the configured channel or DM the bot |
| LLM works | Ask "What's on my calendar tomorrow?" — should respond in 5–15s |
| Ollama offline fallback | Stop Ollama on the Mac, send a message — bot should reply with "🔌 LLM is offline" |
| Scheduled notifications | Wait for 8 PM (or temporarily change the cron in `main.py` to test) |

---

## Troubleshooting

| Problem | Solution |
|---|---|
| Bot can't reach Ollama | Verify MacBook IP, check `OLLAMA_HOST=0.0.0.0` is set, ensure no firewall blocking port 11434 |
| LLM always times out | MacBook may be sleeping — run `caffeinate -s`, check Energy settings |
| NAS can't resolve MacBook hostname | Use the IP address, not a hostname. Set a static IP or DHCP reservation |
| `docker compose` not found on NAS | Use `docker-compose` (hyphenated) or install via Container Manager |
| Container exits immediately | Check `docker compose logs context-bot` — likely a missing env var |
| Bot responds slowly after Mac wake | First query after wake takes longer (model cold-loads ~15–20s). Subsequent queries are fast |

---

## Resource Usage

| Component | Machine | RAM | CPU | Disk |
|---|---|---|---|---|
| context-bot container | NAS | ~60 MB | Minimal | ~80 MB image |
| Ollama + gemma4:e4b | MacBook | ~10 GB | Moderate during inference | ~10 GB model |

The NAS stays under 100 MB total for the bot. The MacBook only uses significant resources during active LLM queries.
