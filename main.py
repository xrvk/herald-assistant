import os
import re
import asyncio
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from icalendar import Calendar
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import NamedTuple, Optional
import apprise
import discord
import recurring_ical_events
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Configuration
TZ = ZoneInfo(os.getenv("TZ", "America/Los_Angeles"))

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e4b")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

# Validate DISCORD_CHANNEL_ID is numeric if set
if DISCORD_CHANNEL_ID and not DISCORD_CHANNEL_ID.strip().isdigit():
    raise RuntimeError(f"DISCORD_CHANNEL_ID must be a numeric ID, got: {DISCORD_CHANNEL_ID!r}")
if DISCORD_CHANNEL_ID:
    DISCORD_CHANNEL_ID = DISCORD_CHANNEL_ID.strip()

# Discord user allowlist (comma-separated user IDs). If set, only these users can interact.
_allowed_raw = os.getenv("DISCORD_ALLOWED_USERS", "")
DISCORD_ALLOWED_USERS = {int(uid.strip()) for uid in _allowed_raw.split(",") if uid.strip()} if _allowed_raw.strip() else set()

# ── Calendar sources ──
# Each calendar is a (label, url) tuple. Labels are used in the LLM context
# so the model knows which calendar an event belongs to.
# Add as many as you want — any ICS URL works (iCloud, Outlook, Google, etc.)

CALENDARS = []

def _add_calendar(env_var, label):
    url = os.getenv(env_var)
    if url:
        url = url.replace("webcal://", "https://")
        CALENDARS.append((label, url))

_add_calendar("ICLOUD_URL", os.getenv("ICLOUD_LABEL", "Personal"))
_add_calendar("OUTLOOK_URL", os.getenv("OUTLOOK_LABEL", "Work"))
_add_calendar("GOOGLE_URL", os.getenv("GOOGLE_LABEL", "Google"))

# Support numbered extra calendars: CALENDAR_1_URL + CALENDAR_1_LABEL, etc.
for i in range(1, 10):
    url = os.getenv(f"CALENDAR_{i}_URL")
    label = os.getenv(f"CALENDAR_{i}_LABEL", f"Calendar {i}")
    if url:
        CALENDARS.append((label, url.replace("webcal://", "https://")))

if not CALENDARS:
    raise RuntimeError("No calendar URLs configured. Set at least one of: ICLOUD_URL, OUTLOOK_URL, GOOGLE_URL, or CALENDAR_1_URL")

print(f"Loaded {len(CALENDARS)} calendar(s): {', '.join(label for label, _ in CALENDARS)}")

# Which calendar labels count as "work" (used for weeknight digest + LLM context)
WORK_LABELS = {l.strip() for l in os.getenv("WORK_LABELS", "Work").split(",")}

_work_cals = [label for label, _ in CALENDARS if label in WORK_LABELS]
_personal_cals = [label for label, _ in CALENDARS if label not in WORK_LABELS]
if _work_cals:
    print(f"  Work calendars: {', '.join(_work_cals)}")
if _personal_cals:
    print(f"  Personal calendars: {', '.join(_personal_cals)}")

# Events to ignore (case-insensitive substring match, comma-separated)
_ignored_raw = os.getenv("IGNORED_EVENTS", "")
IGNORED_EVENTS = [e.strip().lower() for e in _ignored_raw.split(",") if e.strip()] if _ignored_raw.strip() else []

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
    h, m = time_str.split(":")
    return (days.strip(), int(h), int(m))

# Weeknight digest: tomorrow's work events (default: Sun-Thu 20:00)
_weeknight = _parse_schedule(os.getenv("WEEKNIGHT_SCHEDULE", "sun,mon,tue,wed,thu 20:00"), "sun,mon,tue,wed,thu", "20:00")

# Weekend preview: Fri-Sun events (default: Thu 16:00)
_weekend = _parse_schedule(os.getenv("WEEKEND_SCHEDULE", "thu 16:00"), "thu", "16:00")

# Setup Apprise (only required when scheduled digests are enabled)
_schedules_enabled = _weeknight is not None or _weekend is not None
_apprise_url = os.getenv("APPRISE_URL")
if _schedules_enabled:
    if not _apprise_url:
        raise RuntimeError(
            "APPRISE_URL is required when scheduled digests are enabled. "
            "Set APPRISE_URL or disable both schedules (WEEKNIGHT_SCHEDULE=off WEEKEND_SCHEDULE=off)."
        )
    apobj = apprise.Apprise()
    apobj.add(_apprise_url)
else:
    apobj = None

# ── LLM / prompt configuration ──
CONTEXT_DAYS = int(os.getenv("CONTEXT_DAYS", "7"))
HISTORY_DAYS = int(os.getenv("HISTORY_DAYS", "10"))
HISTORY_CACHE_TTL = int(os.getenv("HISTORY_CACHE_TTL", "21600"))  # 6 hours
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", (
    "You are a helpful personal calendar assistant. You have access to the user's "
    "calendar events from multiple calendars. Each event is prefixed with a "
    "label in brackets (e.g. [Work], [Personal]). A legend at the top of the calendar "
    "data tells you which calendars are work vs personal. Use this to give context-aware "
    "answers — for example, distinguish between work meetings and personal plans. "
    "When historical events are included, they appear under a PAST EVENTS section with "
    "relative day markers (e.g. YESTERDAY, 3 DAYS AGO). Use these to answer questions "
    "about what happened recently. "
    "Answer questions concisely and accurately. If asked about availability, check for "
    "gaps between events. Always reference specific dates and times."
))

# ── Calendar helpers ──

class Event(NamedTuple):
    dt: datetime
    summary: str
    duration_min: Optional[int]
    all_day: bool = False

# Calendar cache: avoids re-fetching ICS feeds on every message
_cal_cache = {}       # url -> (Calendar, timestamp)
_CAL_CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))  # seconds

def fetch_events(url):
    """Fetch and parse calendar events, with caching."""
    now = time.time()
    cached = _cal_cache.get(url)
    if cached and (now - cached[1]) < _CAL_CACHE_TTL:
        return cached[0]
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        cal = Calendar.from_ical(response.text)
        _cal_cache[url] = (cal, now)
        return cal
    except requests.RequestException as e:
        print(f"Error fetching calendar: {e}")
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
        summary_lower = summary.lower()
        if IGNORED_EVENTS and any(ignored in summary_lower for ignored in IGNORED_EVENTS):
            continue
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

# Context caches: separate past and future with different TTLs
_future_ctx_cache = {"text": None, "ts": 0, "days": None}
_past_ctx_cache = {"text": None, "ts": 0, "days": None}
_history_verified = False

def _fetch_all_calendars():
    """Fetch all calendar data in parallel. Returns list of (label, Calendar)."""
    cal_data = []
    with ThreadPoolExecutor(max_workers=len(CALENDARS)) as pool:
        futures = [(label, pool.submit(fetch_events, url)) for label, url in CALENDARS]
        for label, future in futures:
            cal = future.result()
            if cal:
                cal_data.append((label, cal))
    return cal_data

def _build_day_sections(cal_data, start, num_days, now):
    """Build formatted sections for a range of days."""
    sections = []
    for offset in range(num_days):
        day_start = (start + timedelta(days=offset)).replace(hour=0, minute=0, second=0)
        day_end = day_start.replace(hour=23, minute=59, second=59)
        day_events = []
        for label, cal in cal_data:
            day_events += [Event(dt=e.dt, summary=f"[{label}] {e.summary}", duration_min=e.duration_min, all_day=e.all_day)
                           for e in get_upcoming_events(cal, day_start, day_end)]
        day_events.sort(key=lambda x: (not x.all_day, x.dt))

        day_label = day_start.strftime('%A, %B %d')
        delta_days = (now.date() - day_start.date()).days
        if delta_days == 0:
            day_label += " (TODAY)"
        elif delta_days == -1:
            day_label += " (TOMORROW)"
        elif delta_days == 1:
            day_label += " (YESTERDAY)"
        elif delta_days > 1:
            day_label += f" ({delta_days} DAYS AGO)"

        if day_events:
            lines = []
            for e in day_events:
                if e.all_day:
                    lines.append(f"  All Day - {e.summary}")
                else:
                    lines.append(f"  {e.dt.strftime('%I:%M %p')} - {e.summary} {_format_duration(e.duration_min)}".rstrip())
            sections.append(f"{day_label}:\n" + "\n".join(lines))
        else:
            sections.append(f"{day_label}:\n  No events")
    return sections

def get_future_context(days_ahead=None):
    """Build a structured text summary of upcoming events for LLM context."""
    if days_ahead is None:
        days_ahead = CONTEXT_DAYS

    now_ts = time.time()
    if _future_ctx_cache["text"] and _future_ctx_cache["days"] == days_ahead and (now_ts - _future_ctx_cache["ts"]) < _CAL_CACHE_TTL:
        return _future_ctx_cache["text"]

    now = datetime.now(TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    cal_data = _fetch_all_calendars()
    sections = _build_day_sections(cal_data, start, days_ahead, now)
    result = "\n\n".join(sections)

    _future_ctx_cache["text"] = result
    _future_ctx_cache["ts"] = now_ts
    _future_ctx_cache["days"] = days_ahead
    return result

def get_past_context(days_back=None):
    """Build a structured text summary of past events for LLM context."""
    global _history_verified
    if days_back is None:
        days_back = HISTORY_DAYS

    now_ts = time.time()
    if _past_ctx_cache["text"] and _past_ctx_cache["days"] == days_back and (now_ts - _past_ctx_cache["ts"]) < HISTORY_CACHE_TTL:
        return _past_ctx_cache["text"]

    now = datetime.now(TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Past range: from (today - days_back) to yesterday 23:59
    past_start = today_start - timedelta(days=days_back)

    cal_data = _fetch_all_calendars()

    # One-time verification: log how many past events each feed returns
    if not _history_verified:
        _history_verified = True
        for label, cal in cal_data:
            past_events = get_upcoming_events(cal, past_start, today_start - timedelta(seconds=1))
            count = len(past_events)
            if count == 0:
                print(f"⚠ Calendar '{label}' returned no past events — history may not be available for this feed.")
            else:
                print(f"  Calendar '{label}': {count} past events in last {days_back} days")

    sections = _build_day_sections(cal_data, past_start, days_back, now)
    result = "\n\n".join(sections)

    _past_ctx_cache["text"] = result
    _past_ctx_cache["ts"] = now_ts
    _past_ctx_cache["days"] = days_back
    return result

def build_context(include_past=False):
    """Combine past and future context with headers and calendar legend."""
    now = datetime.now(TZ)
    header = f"Current time: {now.strftime('%A, %B %d %Y at %I:%M %p')} ({TZ.key})"

    legend_parts = []
    if _work_cals:
        legend_parts.append(f"Work calendars: {', '.join(_work_cals)}")
    if _personal_cals:
        legend_parts.append(f"Personal calendars: {', '.join(_personal_cals)}")
    legend = "\n".join(legend_parts) if legend_parts else ""

    parts = [header]
    if legend:
        parts.append(legend)

    if include_past:
        past = get_past_context()
        parts.append(f"── PAST EVENTS ──\n\n{past}")

    future = get_future_context()
    parts.append(f"── UPCOMING EVENTS ──\n\n{future}")

    return "\n\n".join(parts)

# ── Question classification ──

# Patterns that indicate the user is asking about past events
_PAST_PATTERNS = re.compile(
    r"(?:"
    r"last\s+(?:week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|month|few\s+days)"
    r"|yesterday"
    r"|(?:did|was|were)\s+(?:I|there|we|my)"
    r"|(?:what|how\s+many)\s+.*(?:happened|had|did|last|past|earlier|previous|ago)"
    r"|\bago\b"
    r"|\bpast\s+(?:week|few|couple|day)"
    r"|\bearlier\s+(?:this\s+week|today)"
    r"|\brecap\b|\breview\b|\bsummary\s+of\s+(?:last|this)\b"
    r"|\bhow\s+(?:was|did)\b"
    r"|\bthis\s+(?:past|morning)\b"
    r")",
    re.IGNORECASE,
)

# Patterns that clearly indicate future-only questions
_FUTURE_PATTERNS = re.compile(
    r"(?:"
    r"\btomorrow\b|\bnext\s+(?:week|monday|tuesday|wednesday|thursday|friday|saturday|sunday|month)"
    r"|\bupcoming\b|\bschedule\b|\bwhat'?s\s+(?:on|coming|ahead|planned)"
    r"|\bam\s+I\s+(?:free|busy|available)\b"
    r"|\bdo\s+I\s+have\b"
    r"|\bthis\s+(?:week|weekend)\b"
    r"|\btonight\b|\blater\b"
    r")",
    re.IGNORECASE,
)

def classify_question(question):
    """Classify whether a question needs past events, future events, or both.
    
    Returns 'past', 'future', or 'ambiguous'.
    Tier 1: fast keyword regex. Tier 2: LLM fallback for ambiguous.
    """
    has_past = bool(_PAST_PATTERNS.search(question))
    has_future = bool(_FUTURE_PATTERNS.search(question))

    if has_past and not has_future:
        return "past"
    if has_future and not has_past:
        return "future"
    if has_past and has_future:
        return "past"  # mixed signals → include history to be safe

    # Tier 2: LLM fallback for ambiguous questions
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": (
                        "Classify this question as needing 'past' calendar events, "
                        "'future' calendar events, or 'both'. Respond with ONLY one word: "
                        "past, future, or both."
                    )},
                    {"role": "user", "content": question},
                ],
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 16, "num_ctx": 512},
            },
            timeout=15,
        )
        resp.raise_for_status()
        answer = resp.json()["message"]["content"].strip().lower()
        if "past" in answer or "both" in answer:
            return "past"
        return "future"
    except Exception as e:
        print(f"Classification fallback failed: {e}")
        return "future"  # safe default

# ── LLM ──

def ask_llm(question, calendar_context, include_past=False):
    """Send a question + calendar context to Ollama and return the response."""
    num_ctx = 12288 if include_past else 8192
    system_prompt = f"{SYSTEM_PROMPT}\n\nCALENDAR DATA:\n{calendar_context}"
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 512,
                    "num_ctx": num_ctx,
                },
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]
    except requests.exceptions.ConnectionError:
        return "🔌 LLM is offline — Ollama may not be running or is unreachable."
    except requests.exceptions.Timeout:
        return "⏳ LLM timed out — the model may still be loading. Try again in a minute."
    except Exception as e:
        print(f"LLM error: {e}")
        return "❌ Something went wrong with the LLM. Check server logs."

# ── Scheduled notifications ──

def send_work_update():
    """Triggered weeknights for tomorrow's work schedule."""
    print("Fetching fresh work schedule...")
    now = datetime.now(TZ)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0)
    end_of_tomorrow = tomorrow.replace(hour=23, minute=59, second=59)

    all_events = []
    for label, url in CALENDARS:
        if label not in WORK_LABELS:
            continue
        cal = fetch_events(url)
        if cal:
            all_events += get_upcoming_events(cal, tomorrow, end_of_tomorrow)

    all_events.sort(key=lambda x: x.dt)
    lines = format_events_for_notification(all_events)
    msg = "\n".join(lines) if lines else "No meetings tomorrow! 🎉"

    result = apobj.notify(
        title=f"📅 Work Schedule for {tomorrow.strftime('%A, %b %d')}",
        body=msg,
    )
    if not result:
        print("Warning: Weeknight digest notification failed to send.")

def send_weekend_update():
    """Triggered Thursday for weekend context."""
    print("Fetching weekend plans...")
    cal_data = []
    for label, url in CALENDARS:
        cal = fetch_events(url)
        if cal:
            cal_data.append((label, cal))

    now = datetime.now(TZ)
    # Find the upcoming Friday (or today if it's already Friday)
    days_until_friday = (4 - now.weekday()) % 7
    friday = (now + timedelta(days=days_until_friday)).replace(hour=0, minute=0, second=0)

    sections = []
    for offset in range(3):
        day_start = (friday + timedelta(days=offset)).replace(hour=0, minute=0, second=0)
        day_end = day_start.replace(hour=23, minute=59, second=59)
        day_events = []
        for label, cal in cal_data:
            day_events += get_upcoming_events(cal, day_start, day_end)
        day_events = sorted(set(day_events), key=lambda x: x.dt)
        label = day_start.strftime("%A, %b %d")
        lines = format_events_for_notification(day_events)
        if lines:
            sections.append(f"── {label} ──\n" + "\n".join(lines))
        else:
            sections.append(f"── {label} ──\nNothing planned")

    msg = "\n\n".join(sections)

    result = apobj.notify(
        title="🚀 Weekend Preview (Fri-Sun)",
        body=msg,
    )
    if not result:
        print("Warning: Weekend preview notification failed to send.")

# ── Discord bot ──

def _keep_model_alive():
    """Ping Ollama to keep the model loaded in GPU memory."""
    try:
        requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": [], "keep_alive": "10m"},
            timeout=5,
        )
    except Exception:
        pass

async def _async_keep_model_alive():
    await asyncio.to_thread(_keep_model_alive)

async def _async_send_work_update():
    await asyncio.to_thread(send_work_update)

async def _async_send_weekend_update():
    await asyncio.to_thread(send_weekend_update)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

_scheduler_started = False
_ready_at = None

@client.event
async def on_ready():
    global _scheduler_started, _ready_at
    _ready_at = datetime.now(TZ)
    print(f"Discord bot logged in as {client.user}")
    if DISCORD_ALLOWED_USERS:
        print(f"  Allowed users: {DISCORD_ALLOWED_USERS}")
    if not _scheduler_started:
        scheduler = AsyncIOScheduler(timezone=TZ)
        if _weeknight:
            days, h, m = _weeknight
            scheduler.add_job(_async_send_work_update, "cron", day_of_week=days, hour=h, minute=m)
            print(f"  Weeknight digest: {days} at {h:02d}:{m:02d}")
        if _weekend:
            days, h, m = _weekend
            scheduler.add_job(_async_send_weekend_update, "cron", day_of_week=days, hour=h, minute=m)
            print(f"  Weekend preview: {days} at {h:02d}:{m:02d}")
        # Keep the LLM model loaded in memory (ping every 4 minutes)
        scheduler.add_job(_async_keep_model_alive, "interval", minutes=4)
        scheduler.start()
        _scheduler_started = True
        print("Scheduler started.")

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

    print(f"[Chat] {message.author}: {question}")
    async with message.channel.typing():
        # Classify whether the question needs past events (skip if history disabled)
        include_past = False
        if HISTORY_DAYS > 0:
            classification = await asyncio.to_thread(classify_question, question)
            include_past = classification == "past"
            if include_past:
                print(f"[Chat] Including past events (classification: {classification})")
        # Run blocking calendar fetch + LLM call in a thread
        calendar_context = await asyncio.to_thread(build_context, include_past)
        answer = await asyncio.to_thread(ask_llm, question, calendar_context, include_past)

    print(f"[Chat] Reply ({len(answer)} chars): {answer[:100]}...")
    # Discord has a 2000 char limit
    if len(answer) > 1900:
        answer = answer[:1900] + "\n…(truncated)"

    await message.reply(answer)

# ── Main entry point ──

async def run_scheduler_only():
    """Run just the scheduler when no Discord token is configured."""
    scheduler = AsyncIOScheduler(timezone=TZ)
    if _weeknight:
        days, h, m = _weeknight
        scheduler.add_job(_async_send_work_update, "cron", day_of_week=days, hour=h, minute=m)
        print(f"  Weeknight digest: {days} at {h:02d}:{m:02d}")
    if _weekend:
        days, h, m = _weekend
        scheduler.add_job(_async_send_weekend_update, "cron", day_of_week=days, hour=h, minute=m)
        print(f"  Weekend preview: {days} at {h:02d}:{m:02d}")
    scheduler.start()
    print("Context Bot Started. Scheduler running (no Discord chat).")
    # Keep the loop alive
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    if DISCORD_BOT_TOKEN:
        print("Starting Discord bot + scheduler...")
        client.run(DISCORD_BOT_TOKEN)
    else:
        print("DISCORD_BOT_TOKEN not set — running scheduler only (no chat).")
        asyncio.run(run_scheduler_only())