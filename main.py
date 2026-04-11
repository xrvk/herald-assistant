import asyncio

from scout_report.config import (
    TZ, DISCORD_BOT_TOKEN,
    _weeknight, _weekend, _signal_chat_enabled,
)
from scout_report.notifications import (
    async_send_work_update, async_send_weekend_update,
)


async def run_without_discord():
    """Run scheduler + optional Signal chat when no Discord token is configured."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler(timezone=TZ)
    if _weeknight:
        days, h, m = _weeknight
        scheduler.add_job(async_send_work_update, "cron", day_of_week=days, hour=h, minute=m)
        print(f"  Weeknight digest: {days} at {h:02d}:{m:02d}")
    if _weekend:
        days, h, m = _weekend
        scheduler.add_job(async_send_weekend_update, "cron", day_of_week=days, hour=h, minute=m)
        print(f"  Weekend preview: {days} at {h:02d}:{m:02d}")
    scheduler.start()

    if _signal_chat_enabled:
        from scout_report.signal_bot import ws_loop
        asyncio.create_task(ws_loop())
        print("Scout Report Started. Scheduler + Signal chat running (no Discord).")
    else:
        print("Scout Report Started. Scheduler running (no Discord chat).")

    # Keep the loop alive
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    if DISCORD_BOT_TOKEN:
        print("Starting Discord bot + scheduler...")
        from scout_report.discord_bot import client
        client.run(DISCORD_BOT_TOKEN)
    else:
        print("DISCORD_BOT_TOKEN not set — running scheduler only (no chat).")
        asyncio.run(run_without_discord())