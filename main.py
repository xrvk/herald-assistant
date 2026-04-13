import os
import sys
import json
import asyncio
import atexit
import signal
import time
import threading
import requests
from concurrent.futures import ThreadPoolExecutor
from icalendar import Calendar
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import NamedTuple, Optional
import apprise
import recurring_ical_events
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Configuration
TZ = ZoneInfo(os.getenv("TZ", "America/Los_Angeles"))

# ── Calendar sources ──
# Each calendar is a (label, url) tuple.
# Add as many as you want — any ICS URL works (iCloud, Outlook, Google, etc.)

CALENDARS = []

def _add_calendar(env_var, label):
    url = os.getenv(env_var)
    if url:
        url = url.replace("webcal://", "https://")
        if not url.startswith(("http://", "https://")):
            raise RuntimeError(f"Calendar URL for {label} must use http:// or https:// scheme, got: {url[:30]}...")
        CALENDARS.append((label, url))

_add_calendar("ICLOUD_URL", os.getenv("ICLOUD_LABEL", "iCloud"))
_add_calendar("OUTLOOK_URL", os.getenv("OUTLOOK_LABEL", "Outlook"))
_add_calendar("GOOGLE_URL", os.getenv("GOOGLE_LABEL", "Google"))

# Support numbered extra calendars: CALENDAR_1_URL + CALENDAR_1_LABEL, etc.
for i in range(1, 10):
    _add_calendar(f"CALENDAR_{i}_URL", os.getenv(f"CALENDAR_{i}_LABEL", f"Calendar {i}"))

if not CALENDARS:
    raise RuntimeError("No calendar URLs configured. Set at least one of: ICLOUD_URL, OUTLOOK_URL, GOOGLE_URL, or CALENDAR_1_URL")

print(f"Loaded {len(CALENDARS)} calendar(s): {', '.join(label for label, _ in CALENDARS)}")

# Which calendar labels count as "work" (used for weeknight digest)
WORK_LABELS = {l.strip() for l in os.getenv("WORK_LABELS", "").split(",") if l.strip()}

_work_cals = [label for label, _ in CALENDARS if label in WORK_LABELS]
_personal_cals = [label for label, _ in CALENDARS if label not in WORK_LABELS]
if _work_cals:
    print(f"  Work calendars: {', '.join(_work_cals)}")
if _personal_cals:
    print(f"  Personal calendars: {', '.join(_personal_cals)}")

# ── Schedule configuration ──
# Format: "days HH:MM" or "off" to disable
# days = comma-separated APScheduler day names (mon,tue,wed,thu,fri,sat,sun)

def _parse_schedule(raw, default_days, default_time):
    """Parse 'days HH:MM' into (days, hour, minute) or None if disabled."""
    if raw.strip().lower() in ("off", "false", "disabled", "none", ""):
        return None
    parts = raw.strip().rsplit(" ", 1)
    if len(parts) == 2:
        days, time_str = parts
    elif ":" in parts[0]:
        days, time_str = default_days, parts[0]
    else:
        days, time_str = parts[0], default_time
    h, m = int(time_str.split(":")[0]), int(time_str.split(":")[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Invalid time: {h:02d}:{m:02d} — hour must be 0-23, minute 0-59")
    return (days.strip(), h, m)

def _schedule_to_str(sched):
    """Convert (days, hour, minute) tuple to 'days HH:MM' string, or 'off' if None."""
    if sched is None:
        return "off"
    return f"{sched[0]} {sched[1]:02d}:{sched[2]:02d}"

# ── Schedule persistence ──
_SCHEDULES_FILE = os.getenv("SCHEDULES_PATH", os.path.join(os.path.dirname(__file__) or ".", "data", "schedules.json"))

def _load_schedules():
    """Load schedule overrides from JSON file. Returns dict or None."""
    try:
        with open(_SCHEDULES_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def _save_schedules(data):
    """Save schedule overrides to JSON file."""
    os.makedirs(os.path.dirname(_SCHEDULES_FILE) or ".", exist_ok=True)
    with open(_SCHEDULES_FILE, "w") as f:
        json.dump(data, f, indent=2)

# Parse env var defaults
try:
    _weeknight = _parse_schedule(os.getenv("WEEKNIGHT_SCHEDULE", "off"), "sun,mon,tue,wed,thu", "20:00")
except (ValueError, IndexError) as e:
    raise RuntimeError(f"Invalid WEEKNIGHT_SCHEDULE: {os.getenv('WEEKNIGHT_SCHEDULE')!r} — {e}") from e

try:
    _weekend = _parse_schedule(os.getenv("WEEKEND_SCHEDULE", "off"), "thu", "16:00")
except (ValueError, IndexError) as e:
    raise RuntimeError(f"Invalid WEEKEND_SCHEDULE: {os.getenv('WEEKEND_SCHEDULE')!r} — {e}") from e

try:
    _noon_brief = _parse_schedule(os.getenv("NOON_SCHEDULE", "off"), "sun,mon,tue,wed,thu", "12:00")
except (ValueError, IndexError) as e:
    raise RuntimeError(f"Invalid NOON_SCHEDULE: {os.getenv('NOON_SCHEDULE')!r} — {e}") from e

# Overlay with persisted schedule overrides (from .schedule commands)
_saved = _load_schedules()
if _saved:
    print("Loaded schedule overrides from schedules.json")
    if "weeknight" in _saved:
        _weeknight = _parse_schedule(_saved["weeknight"], "sun,mon,tue,wed,thu", "20:00")
    if "weekend" in _saved:
        _weekend = _parse_schedule(_saved["weekend"], "thu", "16:00")
    if "noon" in _saved:
        _noon_brief = _parse_schedule(_saved["noon"], "sun,mon,tue,wed,thu", "12:00")

# At least one schedule must be enabled (this is a notification-only bot)
_schedules_enabled = _weeknight is not None or _weekend is not None or _noon_brief is not None
if not _schedules_enabled:
    raise RuntimeError(
        "No schedules enabled — this is a notification-only bot.\n"
        "Enable at least one: WEEKNIGHT_SCHEDULE, WEEKEND_SCHEDULE, or NOON_SCHEDULE.\n"
        "Example: WEEKNIGHT_SCHEDULE='sun,mon,tue,wed,thu 20:00'"
    )

# Setup Apprise (required — this is a notification-only bot)
_apprise_url = os.getenv("APPRISE_URL")
if not _apprise_url:
    raise RuntimeError(
        "APPRISE_URL is required for scheduled digests.\n"
        "Discord webhook example: discord://webhook_id/webhook_token\n"
        "See https://github.com/caronc/apprise for supported services."
    )
apobj = apprise.Apprise()
apobj.add(_apprise_url)

# ── Startup summary ──
print("─" * 40)
if _apprise_url:
    print(f"  Notifications: enabled ({_apprise_url[:20]}...)" if len(_apprise_url) > 20 else "  Notifications: enabled")
print(f"  Timezone: {TZ.key}")
print("─" * 40)

# ── Calendar helpers ──

class Event(NamedTuple):
    dt: datetime
    summary: str
    duration_min: Optional[int]
    all_day: bool = False

# Calendar cache: avoids re-fetching ICS feeds on every digest
_cal_cache = {}       # url -> (Calendar, timestamp)
_CAL_CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))  # seconds
_cal_lock = threading.Lock()  # protects _cal_cache, CALENDARS, _cal_labels
_cal_fetch_locks: dict[str, threading.Lock] = {}  # per-URL locks to prevent stampede

# Reverse lookup: url -> label (for safe logging without exposing URL tokens)
_cal_labels = {url: label for label, url in CALENDARS}

def fetch_events(url):
    """Fetch and parse calendar events, with caching and stampede prevention."""
    now = time.time()
    with _cal_lock:
        cached = _cal_cache.get(url)
    if cached and (now - cached[1]) < _CAL_CACHE_TTL:
        return cached[0]
    # Per-URL lock: only one thread fetches a given URL at a time (stampede prevention)
    with _cal_lock:
        if url not in _cal_fetch_locks:
            _cal_fetch_locks[url] = threading.Lock()
        url_lock = _cal_fetch_locks[url]
    with url_lock:
        # Re-check cache — another thread may have fetched while we waited
        with _cal_lock:
            cached = _cal_cache.get(url)
        if cached and (now - cached[1]) < _CAL_CACHE_TTL:
            return cached[0]
        cal_label = _cal_labels.get(url, "unknown")
        last_err = None
        for attempt in range(2):  # 1 try + 1 retry
            try:
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                cal = Calendar.from_ical(response.text)
                with _cal_lock:
                    _cal_cache[url] = (cal, time.time())
                return cal
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                last_err = e
                if attempt == 0:
                    print(f"[Retry] Calendar '{cal_label}': {type(e).__name__}, retrying in 2s...")
                    time.sleep(2)
                    continue
            except requests.RequestException as e:
                status = getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None
                if attempt == 0 and status and status >= 500:
                    print(f"[Retry] Calendar '{cal_label}': HTTP {status}, retrying in 2s...")
                    last_err = e
                    time.sleep(2)
                    continue
                last_err = e
                break  # 4xx or second attempt — don't retry
        # All attempts failed
        if last_err:
            status_info = getattr(last_err, 'response', None)
            status_str = str(status_info.status_code) if status_info else 'connection failed'
            print(f"Error fetching calendar '{cal_label}': {type(last_err).__name__}: {status_str}")
        # Return stale cache on error if available
        if cached:
            return cached[0]
        return None

def get_upcoming_events(calendar, start_date, end_date):
    """Filter events within a specific time range, expanding recurring events."""
    events = []
    for component in recurring_ical_events.of(calendar).between(start_date, end_date):
        if component.name != "VEVENT":
            continue
        dtstart = component.get("dtstart")
        if not dtstart:
            continue
        dt = dtstart.dt
        is_all_day = isinstance(dt, date) and not isinstance(dt, datetime)
        if is_all_day:
            dt = datetime.combine(dt, datetime.min.time()).replace(tzinfo=TZ)
        else:
            dt = dt.astimezone(TZ)
        summary = str(component.get("summary", "No Title")).replace("\n", " ").replace("\r", " ")
        # Calculate duration in minutes
        duration_min = None
        if not is_all_day:
            dtend = component.get("dtend")
            if dtend:
                end_dt = dtend.dt
                if not isinstance(end_dt, datetime):
                    end_dt = datetime.combine(end_dt, datetime.min.time()).replace(tzinfo=TZ)
                else:
                    end_dt = end_dt.astimezone(TZ)
                duration_min = int((end_dt - dt).total_seconds() / 60)
            elif component.get("duration"):
                duration_min = int(component.get("duration").dt.total_seconds() / 60)
        events.append(Event(dt=dt, summary=summary, duration_min=duration_min, all_day=is_all_day))
    return sorted(events, key=lambda x: x.dt)

def _format_duration(minutes):
    """Format duration in minutes to a human-readable string."""
    if minutes is None:
        return ""
    if minutes < 60:
        return f"({minutes}m)"
    hours, mins = divmod(minutes, 60)
    if mins == 0:
        return f"({hours}h)"
    return f"({hours}h {mins}m)"

def format_events_for_notification(events):
    """Format events as bullet list for Apprise notifications."""
    lines = []
    for event in events:
        if event.all_day:
            lines.append(f"• All Day: {event.summary}")
        else:
            dur = _format_duration(event.duration_min)
            lines.append(f"• {event.dt.strftime('%I:%M %p')}: {event.summary} {dur}".rstrip())
    return lines

_cal_executor = ThreadPoolExecutor(max_workers=max(len(CALENDARS), 1))

def _fetch_all_calendars():
    """Fetch all calendar data in parallel. Returns list of (label, Calendar)."""
    with _cal_lock:
        cals_snapshot = list(CALENDARS)
    cal_data = []
    futures = [(label, _cal_executor.submit(fetch_events, url)) for label, url in cals_snapshot]
    for label, future in futures:
        try:
            cal = future.result()
            if cal:
                cal_data.append((label, cal))
        except Exception as e:
            print(f"Error loading calendar '{label}': {e}")
    return cal_data

# ── Scheduled notifications ──

def _send_notification(title, body, digest_name="notification"):
    """Send an Apprise notification with error handling."""
    result = apobj.notify(title=title, body=body)
    if not result:
        print(f"Warning: {digest_name} notification failed to send.")

def _fetch_digest_events(start, end, label_filter=None):
    """Fetch events for digest notifications, optionally filtering by label."""
    cal_data = _fetch_all_calendars()
    all_events = []
    for label, cal in cal_data:
        if label_filter and label not in label_filter:
            continue
        all_events += get_upcoming_events(cal, start, end)
    return sorted(all_events, key=lambda x: x.dt)

def send_work_update():
    """Triggered weeknights for tomorrow's work schedule."""
    try:
        print("Fetching fresh work schedule...")
        now = datetime.now(TZ)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0)
        end_of_tomorrow = tomorrow.replace(hour=23, minute=59, second=59)

        all_events = _fetch_digest_events(tomorrow, end_of_tomorrow, WORK_LABELS)
        lines = format_events_for_notification(all_events)
        msg = "\n".join(lines) if lines else "No meetings tomorrow! 🎉"

        _send_notification(
            f"📅 Work Schedule for {tomorrow.strftime('%A, %b %d')}",
            msg, "Weeknight digest",
        )
    except Exception as e:
        print(f"Error in weeknight digest: {e}")

def send_noon_brief():
    """Triggered at noon on days before workdays — only notifies if tomorrow has work events."""
    try:
        print("Noon brief: checking tomorrow's work events...")
        now = datetime.now(TZ)
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0)
        end_of_tomorrow = tomorrow.replace(hour=23, minute=59, second=59)

        all_events = _fetch_digest_events(tomorrow, end_of_tomorrow, WORK_LABELS)
        if not all_events:
            print("Noon brief: no work events tomorrow — skipping notification.")
            return

        lines = format_events_for_notification(all_events)
        msg = "\n".join(lines)

        _send_notification(
            f"📋 Tomorrow's Work: {tomorrow.strftime('%A, %b %d')}",
            msg, "Noon brief",
        )
    except Exception as e:
        print(f"Error in noon brief: {e}")

def send_weekend_update():
    """Triggered Thursday for weekend context."""
    try:
        print("Fetching weekend plans...")
        now = datetime.now(TZ)
        # Find the upcoming Friday (or today if it's already Friday)
        days_until_friday = (4 - now.weekday()) % 7
        friday = (now + timedelta(days=days_until_friday)).replace(hour=0, minute=0, second=0)

        sections = []
        for offset in range(3):
            day_start = (friday + timedelta(days=offset)).replace(hour=0, minute=0, second=0)
            day_end = day_start.replace(hour=23, minute=59, second=59)
            day_events = _fetch_digest_events(day_start, day_end)
            day_events = sorted(set(day_events), key=lambda x: x.dt)
            label = day_start.strftime("%A, %b %d")
            lines = format_events_for_notification(day_events)
            if lines:
                sections.append(f"── {label} ──\n" + "\n".join(lines))
            else:
                sections.append(f"── {label} ──\nNothing planned")

        msg = "\n\n".join(sections)

        _send_notification(
            "🚀 Weekend Preview (Fri-Sun)",
            msg, "Weekend preview",
        )
    except Exception as e:
        print(f"Error in weekend preview digest: {e}")

# ── Scheduler ──

def _make_async(fn):
    """Wrap a sync function for use with APScheduler's AsyncIOScheduler."""
    async def wrapper():
        await asyncio.to_thread(fn)
    wrapper.__name__ = fn.__name__
    return wrapper

_async_send_work_update = _make_async(send_work_update)
_async_send_noon_brief = _make_async(send_noon_brief)
_async_send_weekend_update = _make_async(send_weekend_update)

def _fmt_ampm(h, m):
    """Format hour/minute as 12-hour AM/PM string."""
    period = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {period}"

_scheduler = None  # global reference for rebuild

def _configure_scheduler():
    """Create and configure the APScheduler instance with digest jobs."""
    scheduler = AsyncIOScheduler(timezone=TZ)
    if _weeknight:
        days, h, m = _weeknight
        scheduler.add_job(_async_send_work_update, "cron", day_of_week=days, hour=h, minute=m)
        print(f"  Weeknight digest: {days} at {_fmt_ampm(h, m)}")
    if _noon_brief:
        days, h, m = _noon_brief
        scheduler.add_job(_async_send_noon_brief, "cron", day_of_week=days, hour=h, minute=m)
        print(f"  Noon brief: {days} at {_fmt_ampm(h, m)}")
    if _weekend:
        days, h, m = _weekend
        scheduler.add_job(_async_send_weekend_update, "cron", day_of_week=days, hour=h, minute=m)
        print(f"  Weekend preview: {days} at {_fmt_ampm(h, m)}")
    return scheduler

def _rebuild_scheduler():
    """Stop current scheduler and start a new one with updated schedules."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = _configure_scheduler()
    _scheduler.start()

def _format_schedules_summary():
    """Build a summary of current schedules for display."""
    lines = []
    if _weeknight:
        lines.append(f"Weeknight: {_weeknight[0]} at {_fmt_ampm(_weeknight[1], _weeknight[2])}")
    else:
        lines.append("Weeknight: off")
    if _weekend:
        lines.append(f"Weekend: {_weekend[0]} at {_fmt_ampm(_weekend[1], _weekend[2])}")
    else:
        lines.append("Weekend: off")
    if _noon_brief:
        lines.append(f"Noon brief: {_noon_brief[0]} at {_fmt_ampm(_noon_brief[1], _noon_brief[2])}")
    else:
        lines.append("Noon brief: off")
    return lines

# ── Graceful shutdown ──

def _shutdown():
    """Clean up resources on exit."""
    print("[Shutdown] Cleaning up...")
    _cal_executor.shutdown(wait=False)

atexit.register(_shutdown)

# ── Main entry point ──

def _startup_message():
    """Build startup notification body."""
    _cal_names = ", ".join(label for label, _ in CALENDARS)
    lines = _format_schedules_summary()
    return (
        f"**Scout Report started** ✅\n"
        f"Calendars: {_cal_names}\n"
        f"Timezone: {TZ.key}\n"
        + "\n".join(lines)
    )

# ── Discord bot mode ──
_DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

def _normalize_quotes(text):
    """Normalize smart quotes to ASCII for mobile keyboard compat."""
    return text.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')

if _DISCORD_TOKEN:
    import discord

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        global _scheduler
        _scheduler = _configure_scheduler()
        _scheduler.start()
        print(f"Discord bot logged in as {client.user}")
        print("Scheduler started.")
        apobj.notify(body=_startup_message(), title="", notify_type="info")

    @client.event
    async def on_message(message):
        if message.author == client.user or message.author.bot:
            return

        content = _normalize_quotes(message.content.strip())
        if not content.startswith("."):
            return

        cmd = content.lower().split()[0]
        args = content[len(cmd):].strip()

        if cmd == ".help":
            await message.channel.send(
                "**Scout Report Commands**\n"
                "`.help` — Show this help\n"
                "`.cal` — List connected calendars\n"
                "`.schedule` — Show current digest schedules\n"
                "`.schedule <type> <days> <HH:MM>` — Update a schedule\n"
                "`.schedule <type> off` — Disable a schedule\n"
                "\n"
                "**Schedule types:** `weeknight`, `weekend`, `noon`\n"
                "**Example:** `.schedule weeknight mon,tue,wed,thu 19:00`"
            )

        elif cmd == ".cal":
            cal_lines = []
            for label, _ in CALENDARS:
                tag = " (work)" if label in WORK_LABELS else ""
                cal_lines.append(f"• {label}{tag}")
            await message.channel.send(
                f"**Connected Calendars ({len(CALENDARS)})**\n" + "\n".join(cal_lines)
            )

        elif cmd == ".schedule":
            global _weeknight, _weekend, _noon_brief
            if not args:
                # Show current schedules
                lines = _format_schedules_summary()
                await message.channel.send("**Current Schedules**\n" + "\n".join(lines))
                return

            parts = args.split(None, 1)
            sched_type = parts[0].lower()
            sched_value = parts[1] if len(parts) > 1 else ""

            if sched_type not in ("weeknight", "weekend", "noon"):
                await message.channel.send(
                    f"Unknown schedule type `{sched_type}`. Use: `weeknight`, `weekend`, or `noon`."
                )
                return

            defaults = {
                "weeknight": ("sun,mon,tue,wed,thu", "20:00"),
                "weekend": ("thu", "16:00"),
                "noon": ("sun,mon,tue,wed,thu", "12:00"),
            }

            try:
                new_sched = _parse_schedule(sched_value or "off", *defaults[sched_type])
            except (ValueError, IndexError) as e:
                await message.channel.send(f"Invalid schedule: {e}")
                return

            # Update in-memory schedule
            if sched_type == "weeknight":
                _weeknight = new_sched
            elif sched_type == "weekend":
                _weekend = new_sched
            elif sched_type == "noon":
                _noon_brief = new_sched

            # Persist to file
            saved = _load_schedules() or {}
            saved[sched_type] = _schedule_to_str(new_sched)
            _save_schedules(saved)

            # Rebuild scheduler
            _rebuild_scheduler()

            if new_sched:
                confirm = f"✅ {sched_type.title()} schedule updated: {new_sched[0]} at {_fmt_ampm(new_sched[1], new_sched[2])}"
            else:
                confirm = f"✅ {sched_type.title()} schedule disabled."
            await message.channel.send(confirm)

    def run():
        print("Starting Discord bot...")
        client.run(_DISCORD_TOKEN, log_handler=None)

else:
    # ── Headless scheduler-only mode ──
    async def run_scheduler_only():
        """Run the scheduler for cron-based calendar notifications (no Discord bot)."""
        global _scheduler
        loop = asyncio.get_running_loop()
        _scheduler = _configure_scheduler()
        _scheduler.start()
        print("Scout Report started. Scheduler running (headless mode).")
        apobj.notify(body=_startup_message(), title="", notify_type="info")

        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
        _scheduler.shutdown(wait=False)
        print("[Shutdown] Scheduler stopped.")

    def run():
        print("Starting scheduler (no DISCORD_TOKEN — headless mode)...")
        asyncio.run(run_scheduler_only())

if __name__ == "__main__":
    run()
