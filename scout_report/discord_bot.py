import asyncio
from datetime import datetime

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from scout_report.config import (
    TZ, DISCORD_CHANNEL_ID, DISCORD_ALLOWED_USERS,
    _weeknight, _weekend, _KEEP_ALIVE_INTERVAL_MIN, _DISCORD_MSG_LIMIT,
    _signal_chat_enabled,
    get_backend,
)
from scout_report.notifications import (
    async_send_work_update, async_send_weekend_update, async_keep_model_alive,
)
from scout_report.chat import normalize_text, handle_backend_command, process_question

# ── Discord client setup ──
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

_scheduler_started = False
_scheduler_lock = asyncio.Lock()
_ready_at = None


@client.event
async def on_ready():
    global _scheduler_started, _ready_at
    _ready_at = datetime.now(TZ)
    print(f"Discord bot logged in as {client.user}")
    if DISCORD_ALLOWED_USERS:
        print(f"  Allowed users: {DISCORD_ALLOWED_USERS}")
    async with _scheduler_lock:
        if not _scheduler_started:
            scheduler = AsyncIOScheduler(timezone=TZ)
            if _weeknight:
                days, h, m = _weeknight
                scheduler.add_job(async_send_work_update, "cron", day_of_week=days, hour=h, minute=m)
                print(f"  Weeknight digest: {days} at {h:02d}:{m:02d}")
            if _weekend:
                days, h, m = _weekend
                scheduler.add_job(async_send_weekend_update, "cron", day_of_week=days, hour=h, minute=m)
                print(f"  Weekend preview: {days} at {h:02d}:{m:02d}")
            # Keep the Ollama model loaded in memory
            if get_backend() == "ollama":
                scheduler.add_job(async_keep_model_alive, "interval", minutes=_KEEP_ALIVE_INTERVAL_MIN)
            scheduler.start()
            _scheduler_started = True
            print("Scheduler started.")

            # Start Signal interactive chat if configured
            if _signal_chat_enabled:
                from scout_report.signal_bot import ws_loop
                asyncio.create_task(ws_loop())


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    # Ignore messages sent before the bot connected
    if _ready_at and message.created_at.astimezone(TZ) < _ready_at:
        return

    # User allowlist check
    if DISCORD_ALLOWED_USERS and message.author.id not in DISCORD_ALLOWED_USERS:
        return

    # Respond to DMs or messages in the designated channel
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_bot_channel = DISCORD_CHANNEL_ID and str(message.channel.id) == DISCORD_CHANNEL_ID

    if not is_dm and not is_bot_channel:
        return

    question = message.content.strip()
    if not question:
        return

    question = normalize_text(question)

    # Use author ID as channel key for DMs (DM channel IDs can change)
    hist_chan = message.author.id if is_dm else message.channel.id
    hist_key = (hist_chan, message.author.id)

    # !backend command — show or switch LLM backend
    if question.lower().startswith("!backend"):
        parts = question.split(maxsplit=1)
        await handle_backend_command(parts, hist_key, message.reply, markdown=True)
        return

    # Discord-specific reply wrapper: enforces 1900-char limit
    async def discord_reply(answer):
        if len(answer) > _DISCORD_MSG_LIMIT:
            answer = answer[:_DISCORD_MSG_LIMIT] + "\n…(truncated)"
        await message.reply(answer)

    await process_question(
        question,
        hist_key,
        discord_reply,
        typing_ctx=message.channel.typing(),
    )
