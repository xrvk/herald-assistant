import os
import re
import json
import asyncio
import atexit
import signal
import time
import tempfile
import threading
import requests
from concurrent.futures import ThreadPoolExecutor
from icalendar import Calendar
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import NamedTuple, Optional
from collections import deque
import apprise
import discord
from discord import app_commands
import recurring_ical_events
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Configuration
TZ = ZoneInfo(os.getenv("TZ", "America/Los_Angeles"))

# ── LLM Backend selection ──
_llm_backend = os.getenv("LLM_BACKEND", "ollama").lower()
if _llm_backend not in ("ollama", "gemini"):
    raise RuntimeError(f"LLM_BACKEND must be 'ollama' or 'gemini', got: {_llm_backend!r}")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e4b")

_gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
_gemini_client = None
_gemini_api_key = os.getenv("GEMINI_API_KEY")

def _get_gemini_client():
    """Lazy-init Gemini client on first use."""
    global _gemini_client
    if _gemini_client is None:
        if not _gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is required when LLM_BACKEND=gemini. "
                "Get a free key at https://aistudio.google.com/app/apikey "
                "or set LLM_BACKEND=ollama for local inference."
            )
        from google import genai
        _gemini_client = genai.Client(api_key=_gemini_api_key)
    return _gemini_client

def get_backend():
    return _llm_backend

def set_backend(name):
    global _llm_backend
    name = name.lower()
    if name not in ("ollama", "gemini"):
        raise ValueError(f"Backend must be 'ollama' or 'gemini', got: {name!r}")
    if name == "gemini":
        _get_gemini_client()  # validate before switching
    _llm_backend = name

GEMINI_MODELS = {
    "flash-lite": "gemini-2.5-flash-lite",
    "flash":      "gemini-2.5-flash",
}

def get_gemini_model():
    return _gemini_model

def set_gemini_model(name: str):
    global _gemini_model
    if name not in GEMINI_MODELS.values():
        raise ValueError(f"Unknown Gemini model: {name!r}. Valid: {list(GEMINI_MODELS.values())}")
    _gemini_model = name

if _llm_backend == "gemini":
    _get_gemini_client()  # validate at startup
    print(f"LLM backend: Gemini ({get_gemini_model()})")
else:
    print(f"LLM backend: Ollama ({OLLAMA_MODEL} at {OLLAMA_URL})")
    if _gemini_api_key:
        print(f"  (Gemini fallback available: {get_gemini_model()})")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN") or None
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID") or None

# Validate DISCORD_CHANNEL_ID is numeric if set
if DISCORD_CHANNEL_ID and not DISCORD_CHANNEL_ID.strip().isdigit():
    raise RuntimeError(f"DISCORD_CHANNEL_ID must be a numeric ID, got: {DISCORD_CHANNEL_ID!r}")
if DISCORD_CHANNEL_ID:
    DISCORD_CHANNEL_ID = DISCORD_CHANNEL_ID.strip()

# Discord user allowlist (comma-separated user IDs). If set, only these users can interact.
_allowed_raw = os.getenv("DISCORD_ALLOWED_USERS", "")
try:
    DISCORD_ALLOWED_USERS = {int(uid.strip()) for uid in _allowed_raw.split(",") if uid.strip()} if _allowed_raw.strip() else set()
except ValueError as e:
    raise RuntimeError(f"DISCORD_ALLOWED_USERS must be comma-separated numeric IDs, got: {_allowed_raw!r}") from e

# ── Calendar sources ──
# Each calendar is a (label, url) tuple. Labels are used in the LLM context
# so the model knows which calendar an event belongs to.
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
    url = os.getenv(f"CALENDAR_{i}_URL")
    label = os.getenv(f"CALENDAR_{i}_LABEL", f"Calendar {i}")
    if url:
        url = url.replace("webcal://", "https://")
        if not url.startswith(("http://", "https://")):
            raise RuntimeError(f"CALENDAR_{i}_URL must use http:// or https:// scheme, got: {url[:30]}...")
        CALENDARS.append((label, url))

if not CALENDARS:
    raise RuntimeError("No calendar URLs configured. Set at least one of: ICLOUD_URL, OUTLOOK_URL, GOOGLE_URL, or CALENDAR_1_URL")

print(f"Loaded {len(CALENDARS)} calendar(s): {', '.join(label for label, _ in CALENDARS)}")

# Which calendar labels count as "work" (used for weeknight digest + LLM context)
WORK_LABELS = {l.strip() for l in os.getenv("WORK_LABELS", "").split(",") if l.strip()}

_work_cals = [label for label, _ in CALENDARS if label in WORK_LABELS]
_personal_cals = [label for label, _ in CALENDARS if label not in WORK_LABELS]
if _work_cals:
    print(f"  Work calendars: {', '.join(_work_cals)}")
if _personal_cals:
    print(f"  Personal calendars: {', '.join(_personal_cals)}")

# Events to ignore (case-insensitive substring match, comma-separated)
# Entries are normalized: quotes stripped, special chars removed for fuzzy matching.
_ignored_raw = os.getenv("IGNORED_EVENTS", "")
_NORM_RE = re.compile(r"[^\w\s]", re.UNICODE)

def _normalize_event(s: str) -> str:
    """Lowercase, strip quotes and non-alphanumeric chars (keep spaces) for fuzzy matching."""
    s = s.strip().strip('"').strip("'").lower()
    return _NORM_RE.sub("", s).strip()

def _parse_event_list(raw: str) -> list[str]:
    """Parse a comma-separated event list, normalizing each entry."""
    if not raw.strip():
        return []
    return [n for e in raw.split(",") if (n := _normalize_event(e))]

IGNORED_EVENTS = _parse_event_list(_ignored_raw)

# Info events: visible to the LLM but tagged as informational (don't block availability)
# (case-insensitive substring match, comma-separated — same format as IGNORED_EVENTS)
_ie_raw = os.getenv("INFO_EVENTS", os.getenv("NON_BLOCKING_EVENTS", ""))
INFO_EVENTS = _parse_event_list(_ie_raw)

# ── Filter persistence ──
# Runtime-added filter entries are persisted to a JSON file so they survive reboots.
# Path defaults to filters.json next to main.py; override with FILTERS_PATH env var.
# In Docker, set FILTERS_PATH=/app/data/filters.json and mount a volume on /app/data.
FILTERS_PATH = os.getenv("FILTERS_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "filters.json"))

def _save_filters() -> None:
    """Atomically persist all filter entries to FILTERS_PATH."""
    data = {"ignored": list(IGNORED_EVENTS), "infoevent": list(INFO_EVENTS)}
    try:
        dir_ = os.path.dirname(FILTERS_PATH) or "."
        with tempfile.NamedTemporaryFile("w", dir=dir_, delete=False, suffix=".tmp") as tmp:
            json.dump(data, tmp)
            tmp.flush()
            tmp_path = tmp.name
        os.replace(tmp_path, FILTERS_PATH)
    except OSError as e:
        print(f"[Filters] Warning: could not save filters to {FILTERS_PATH}: {e}")

def _load_filters() -> None:
    """Load persisted filter entries from FILTERS_PATH at startup, merging with env."""
    if not os.path.exists(FILTERS_PATH):
        return
    try:
        with open(FILTERS_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        print(f"[Filters] Warning: could not load {FILTERS_PATH}: {e}")
        return
    for entry in data.get("ignored", []):
        norm = _normalize_event(entry)
        if norm and norm not in IGNORED_EVENTS:
            IGNORED_EVENTS.append(norm)
    for entry in data.get("infoevent", data.get("nonblocking", [])):
        norm = _normalize_event(entry)
        if norm and norm not in INFO_EVENTS:
            INFO_EVENTS.append(norm)

_load_filters()

def _add_to_filter(target: list[str], names: list[str]) -> list[str]:
    """Add normalized event names to a filter list, skipping duplicates. Returns added names."""
    added = []
    for name in names:
        norm = _normalize_event(name)
        if norm and norm not in target:
            target.append(norm)
            added.append(norm)
    if added:
        _save_filters()
    return added

def _remove_all_filter(target: list[str]) -> list[str]:
    """Remove all entries from a filter list. Returns the removed names."""
    removed = list(target)
    target.clear()
    if removed:
        _save_filters()
    return removed

def _remove_from_filter(target: list[str], names: list[str]) -> tuple[list[str], list[str]]:
    """Remove specific normalized event names from a filter list.

    Returns (removed, not_found) lists of normalized names.
    """
    removed, not_found = [], []
    for name in names:
        norm = _normalize_event(name)
        if not norm:
            continue
        if norm in target:
            target.remove(norm)
            removed.append(norm)
        else:
            not_found.append(norm)
    if removed:
        _save_filters()
    return removed, not_found

def _extract_events_from_reply(text: str) -> list[str]:
    """Extract potential calendar event names from bullet-pointed lines in a bot reply."""
    events = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] not in ('•', '-', '*'):
            continue
        # Strip bullet and bold markdown
        content = re.sub(r'\*+', '', line.lstrip('•-* ')).strip()
        # Match optional "All Day: " or time prefix (e.g. "10:00 AM: "), capture event name,
        # then strip optional duration suffix like "(30m)" or "(1h 30m)"
        m = re.match(
            r'(?:All\s+Day:\s*)?(?:\d{1,2}:\d{2}\s*(?:AM|PM)\s*[:\-\u2014]+\s*)?(.+?)(?:\s+\(\d+[hm](?:\s+\d+[hm])?\))?\s*$',
            content, re.IGNORECASE,
        )
        if m:
            name = m.group(1).strip().rstrip(':').strip()
            if name and len(name) > 2 and name.lower() not in seen:
                seen.add(name.lower())
                events.append(name)
    return events

# Natural-language patterns for "add X to ignore/non-blocking list"
_NL_IGNORE_RE = re.compile(
    r"^(?:please\s+)?add\s+[\"']?(.+?)[\"']?\s+to\s+(?:the\s+)?ignore[d]?(?:\s+events?)?\s*(?:list|filter)?\s*$",
    re.IGNORECASE,
)
_NL_INFOEVENT_RE = re.compile(
    r"^(?:please\s+)?(?:add|mark)\s+[\"']?(.+?)[\"']?\s+(?:(?:as|to(?:\s+the)?)\s+)?info[\s\-]?(?:event|only)(?:s)?\s*(?:list)?\s*$",
    re.IGNORECASE,
)

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

# Weeknight digest: tomorrow's work events (off unless configured)
try:
    _weeknight = _parse_schedule(os.getenv("WEEKNIGHT_SCHEDULE", "off"), "sun,mon,tue,wed,thu", "20:00")
except (ValueError, IndexError) as e:
    raise RuntimeError(f"Invalid WEEKNIGHT_SCHEDULE: {os.getenv('WEEKNIGHT_SCHEDULE')!r} — {e}") from e

# Weekend preview: Fri-Sun events (off unless configured)
try:
    _weekend = _parse_schedule(os.getenv("WEEKEND_SCHEDULE", "off"), "thu", "16:00")
except (ValueError, IndexError) as e:
    raise RuntimeError(f"Invalid WEEKEND_SCHEDULE: {os.getenv('WEEKEND_SCHEDULE')!r} — {e}") from e

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

# ── Conversation history (follow-up support) ──
CONV_HISTORY_TURNS = int(os.getenv("CONV_HISTORY_TURNS", "3"))
CONV_HISTORY_TTL = int(os.getenv("CONV_HISTORY_TTL", "1800"))  # 30 minutes
CONV_HISTORY_CTX_BUMP = int(os.getenv("CONV_HISTORY_CTX_BUMP", "4096"))
_CONV_ANSWER_TRUNC = 500  # max chars stored per bot response (token budget)

# ── Named constants (avoid magic numbers) ──
_OLLAMA_CTX_BASE = 8192
_OLLAMA_CTX_WITH_PAST = 12288
_MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "512"))
_KEEP_ALIVE_INTERVAL_MIN = 4
_DISCORD_MSG_LIMIT = 1900

# ── User-facing error messages ──
_ERR_OLLAMA_OFFLINE = "🔌 LLM is offline — Ollama may not be running or is unreachable."
_ERR_OLLAMA_TIMEOUT = "⏳ LLM timed out — the model may still be loading. Try again in a minute."
_ERR_GEMINI_RATE_LIMIT = "⏳ Gemini rate limit reached — try again in a minute."
_ERR_GEMINI_AUTH = "🔑 Gemini API key is invalid or expired. Check your GEMINI_API_KEY."
_ERR_LLM_GENERIC = "❌ Something went wrong with the LLM. Check server logs."
_ERR_NO_RESPONSE = "I couldn't generate a response. Try rephrasing your question."

# Keyed by (channel_id, user_id) → deque of (question, answer, timestamp)
_conv_history: dict[tuple[int, int], deque] = {}
_conv_lock = threading.Lock()  # protects _conv_history

# Per-user rate limiting: user_id → last request timestamp
_user_last_request: dict[int, float] = {}
_USER_RATE_LIMIT_SEC = int(os.getenv("USER_RATE_LIMIT_SEC", "5"))  # min seconds between LLM requests

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

# All-day events and INFO_EVENTS are tagged (free) in calendar data
SYSTEM_PROMPT += (
    "\n\nEvents marked (free) are informational — still mention them, "
    "but they do not block the user's availability."
)

# ── Startup summary ──
print("─" * 40)
if DISCORD_BOT_TOKEN:
    print(f"  Discord bot: enabled (channel {DISCORD_CHANNEL_ID})" if DISCORD_CHANNEL_ID else "  Discord bot: enabled (DMs only)")
else:
    print("  Discord bot: disabled (no DISCORD_BOT_TOKEN)")
if _schedules_enabled:
    print(f"  Notifications: enabled ({_apprise_url[:20]}...)" if len(_apprise_url) > 20 else f"  Notifications: enabled")
else:
    print("  Notifications: disabled (schedules off)")
if HISTORY_DAYS > 0:
    print(f"  History: {HISTORY_DAYS} days back")
else:
    print("  History: disabled")
if INFO_EVENTS:
    print(f"  Info events: {', '.join(INFO_EVENTS)}")
print(f"  Filters file: {FILTERS_PATH}")
if not DISCORD_BOT_TOKEN and not _schedules_enabled:
    print("  ⚠ Warning: No Discord bot token and no scheduled digests — nothing to do.")
    print("  Set DISCORD_BOT_TOKEN for chat, or enable schedules + APPRISE_URL for digests.")
print("─" * 40)

# ── Calendar helpers ──

class Event(NamedTuple):
    dt: datetime
    summary: str
    duration_min: Optional[int]
    all_day: bool = False
    normalized_summary: str = ""

# Calendar cache: avoids re-fetching ICS feeds on every message
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
    # Demo URLs are in-memory only — never expire (regenerated on .demo)
    if url.startswith("__demo_"):
        if cached:
            return cached[0]
        return None
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
        summary_norm = _normalize_event(summary)
        if IGNORED_EVENTS and any(ignored in summary_norm for ignored in IGNORED_EVENTS):
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
        events.append(Event(dt=dt, summary=summary, duration_min=duration_min, all_day=is_all_day, normalized_summary=summary_norm))
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
_cal_executor = ThreadPoolExecutor(max_workers=max(len(CALENDARS), 1))

def _resize_executor():
    """Recreate the calendar executor when the calendar count changes (e.g. demo toggle)."""
    global _cal_executor
    _cal_executor.shutdown(wait=False)
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

def _build_day_sections(cal_data, start, num_days, now):
    """Build formatted sections for a range of days."""
    sections = []
    for offset in range(num_days):
        day_start = (start + timedelta(days=offset)).replace(hour=0, minute=0, second=0)
        day_end = day_start.replace(hour=23, minute=59, second=59)
        day_events = []
        for label, cal in cal_data:
            day_events += [Event(dt=e.dt, summary=f"[{label}] {e.summary}", duration_min=e.duration_min, all_day=e.all_day, normalized_summary=e.normalized_summary)
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
                is_nb = INFO_EVENTS and any(nb in e.normalized_summary for nb in INFO_EVENTS)
                tag = " (free)" if e.all_day or is_nb else ""
                if e.all_day:
                    lines.append(f"  All Day - {e.summary}{tag}")
                else:
                    lines.append(f"  {e.dt.strftime('%I:%M %p')} - {e.summary}{tag} {_format_duration(e.duration_min)}".rstrip())
            sections.append(f"{day_label}:\n" + "\n".join(lines))
        else:
            sections.append(f"{day_label}:\n  No events")
    return sections

def _cached_context(cache, ttl, days, builder):
    """Return cached context text if fresh, otherwise rebuild via builder(days)."""
    now_ts = time.time()
    if cache["text"] and cache["days"] == days and (now_ts - cache["ts"]) < ttl:
        return cache["text"]
    result = builder(days)
    cache["text"] = result
    cache["ts"] = now_ts
    cache["days"] = days
    return result

def _build_future(days_ahead):
    now = datetime.now(TZ)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    cal_data = _fetch_all_calendars()
    return "\n\n".join(_build_day_sections(cal_data, start, days_ahead, now))

def get_future_context(days_ahead=None):
    """Build a structured text summary of upcoming events for LLM context."""
    return _cached_context(_future_ctx_cache, _CAL_CACHE_TTL, days_ahead or CONTEXT_DAYS, _build_future)

def _build_past(days_back):
    global _history_verified
    now = datetime.now(TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
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

    return "\n\n".join(_build_day_sections(cal_data, past_start, days_back, now))

def get_past_context(days_back=None):
    """Build a structured text summary of past events for LLM context."""
    return _cached_context(_past_ctx_cache, HISTORY_CACHE_TTL, days_back or HISTORY_DAYS, _build_past)

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
    r"|\bam\s+I\s+(?:free|busy|available|doing)\b"
    r"|\bdo\s+I\s+have\b|\bwhat\s+do\s+I\s+have\b"
    r"|\bthis\s+(?:week|weekend)\b"
    r"|\btonight\b|\blater\b|\bwhen\s+(?:is|am)\b"
    r"|\bhow\s+(?:does|is)\s+my\b"
    r"|\bany\s+(?:meetings|events|plans)\b"
    r"|\bwhat\s+(?:time|meetings|events)\b"
    r")",
    re.IGNORECASE,
)

def classify_question(question):
    """Classify whether a question needs past events, future events, or both.
    
    Returns 'past' or 'future'.
    Uses fast keyword regex matching. Ambiguous questions default to 'future'
    (cheaper than an LLM call and correct for the common case).
    """
    has_past = bool(_PAST_PATTERNS.search(question))
    has_future = bool(_FUTURE_PATTERNS.search(question))

    if has_past and not has_future:
        return "past"
    if has_future and not has_past:
        return "future"
    if has_past and has_future:
        return "past"  # mixed signals → include history to be safe

    # No clear signal — default to future (most common intent)
    return "future"

# ── Conversation history helpers ──

def _get_history(channel_id, user_id):
    """Return non-stale conversation history as [(question, answer), ...]."""
    key = (channel_id, user_id)
    with _conv_lock:
        hist = _conv_history.get(key)
        if not hist:
            return []
        now = time.time()
        # Filter to non-stale entries
        valid = [(q, a) for q, a, ts in hist if (now - ts) < CONV_HISTORY_TTL]
        if not valid:
            del _conv_history[key]
    return valid

def _store_exchange(channel_id, user_id, question, answer):
    """Store a Q&A exchange, truncating the answer for token budget.
    
    Skips storage if the answer is an error message (would confuse follow-ups).
    """
    # Don't store error messages in history — they'd confuse the LLM on follow-ups
    if answer.startswith(("🔌", "⏳", "🔑", "❌", "I couldn't generate")):
        return
    key = (channel_id, user_id)
    truncated = answer[:_CONV_ANSWER_TRUNC] + "…" if len(answer) > _CONV_ANSWER_TRUNC else answer
    with _conv_lock:
        if key not in _conv_history:
            _conv_history[key] = deque(maxlen=CONV_HISTORY_TURNS)
        _conv_history[key].append((question, truncated, time.time()))

def _check_rate_limit(user_id):
    """Return True if user should be rate-limited, False if OK to proceed."""
    if _USER_RATE_LIMIT_SEC <= 0:
        return False
    now = time.time()
    last = _user_last_request.get(user_id, 0)
    if (now - last) < _USER_RATE_LIMIT_SEC:
        return True
    _user_last_request[user_id] = now
    return False

def _cleanup_conv_history():
    """Remove stale conversation history entries to prevent unbounded memory growth."""
    now = time.time()
    with _conv_lock:
        stale_keys = [
            key for key, hist in _conv_history.items()
            if not hist or all((now - ts) >= CONV_HISTORY_TTL for _, _, ts in hist)
        ]
        for key in stale_keys:
            del _conv_history[key]
    if stale_keys:
        print(f"[Conv] Cleaned up {len(stale_keys)} stale history entries")
    # Evict stale cache entries for URLs no longer in CALENDARS
    with _cal_lock:
        active_urls = {url for _, url in CALENDARS}
        stale_urls = [url for url in _cal_cache if url not in active_urls]
        for url in stale_urls:
            del _cal_cache[url]
            _cal_fetch_locks.pop(url, None)
            _cal_labels.pop(url, None)
    # Evict rate-limit entries older than 10× the limit (stale users)
    cutoff = now - (_USER_RATE_LIMIT_SEC * 10)
    stale_users = [uid for uid, ts in _user_last_request.items() if ts < cutoff]
    for uid in stale_users:
        del _user_last_request[uid]
    if stale_urls or stale_users:
        print(f"[Cleanup] Evicted {len(stale_urls)} stale cache entries, {len(stale_users)} stale rate-limit entries")

def _estimate_tokens(text):
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4

# ── LLM ──

def _prepare_ollama_messages(system_prompt, question, history, num_ctx):
    """Build Ollama message list with token-budget-aware history trimming.
    
    Returns (messages, num_ctx) — num_ctx may be bumped if history overflows base.
    """
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        num_predict = _MAX_OUTPUT_TOKENS
        base_tokens = _estimate_tokens(system_prompt) + _estimate_tokens(question)
        remaining = num_ctx - num_predict - base_tokens
        trimmed = []
        for q, a in history:
            cost = _estimate_tokens(q) + _estimate_tokens(a)
            if remaining - cost < 0:
                break
            remaining -= cost
            trimmed.append((q, a))
        # If history was trimmed AND a ctx bump is configured, retry with more room
        if len(trimmed) < len(history) and CONV_HISTORY_CTX_BUMP > 0:
            num_ctx += CONV_HISTORY_CTX_BUMP
            remaining = num_ctx - num_predict - base_tokens
            trimmed = []
            for q, a in history:
                cost = _estimate_tokens(q) + _estimate_tokens(a)
                if remaining - cost < 0:
                    break
                remaining -= cost
                trimmed.append((q, a))
            print(f"[Conv] Bumped num_ctx to {num_ctx} to fit history")
        if len(trimmed) < len(history):
            print(f"[Conv] Trimmed history from {len(history)} to {len(trimmed)} exchanges (token budget)")
        for q, a in trimmed:
            messages.append({"role": "user", "content": q})
            messages.append({"role": "assistant", "content": a})
    messages.append({"role": "user", "content": question})
    return messages, num_ctx

def ask_llm(question, calendar_context, include_past=False, history=None):
    """Send a question + calendar context to Ollama and return the response."""
    num_ctx = _OLLAMA_CTX_WITH_PAST if include_past else _OLLAMA_CTX_BASE
    system_prompt = f"{SYSTEM_PROMPT}\n\nCALENDAR DATA:\n{calendar_context}"
    messages, num_ctx = _prepare_ollama_messages(system_prompt, question, history, num_ctx)

    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": _MAX_OUTPUT_TOKENS,
                    "num_ctx": num_ctx,
                },
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        is_timeout = isinstance(e, requests.exceptions.Timeout)
        if _gemini_api_key:
            reason = "timed out" if is_timeout else "offline"
            print(f"[LLM] Ollama {reason} — one-time fallback to Gemini ({get_gemini_model()})")
            return ask_gemini(question, calendar_context, history=history)
        return _ERR_OLLAMA_TIMEOUT if is_timeout else _ERR_OLLAMA_OFFLINE
    except Exception as e:
        print(f"LLM error: {e}")
        return _ERR_LLM_GENERIC

def ask_gemini(question, calendar_context, history=None):
    """Send a question + calendar context to Gemini and return the response."""
    from google.genai import types
    system_prompt = f"{SYSTEM_PROMPT}\n\nCALENDAR DATA:\n{calendar_context}"
    # Build multi-turn contents list
    contents = []
    if history:
        for q, a in history:
            contents.append(types.Content(role="user", parts=[types.Part(text=q)]))
            contents.append(types.Content(role="model", parts=[types.Part(text=a)]))
    contents.append(types.Content(role="user", parts=[types.Part(text=question)]))
    try:
        resp = _get_gemini_client().models.generate_content(
            model=get_gemini_model(),
            contents=contents,
            config={
                "system_instruction": system_prompt,
                "temperature": 0.3,
                "max_output_tokens": _MAX_OUTPUT_TOKENS,
            },
        )
        text = resp.text
        if text:
            return text
        return _ERR_NO_RESPONSE
    except Exception as e:
        error_msg = str(e).lower()
        if "429" in error_msg or "resource_exhausted" in error_msg:
            print(f"Gemini rate limited — returning error to user")
            return _ERR_GEMINI_RATE_LIMIT
        if "api key" in error_msg or "401" in error_msg or "403" in error_msg:
            return _ERR_GEMINI_AUTH
        print(f"Gemini error: {e}")
        return _ERR_LLM_GENERIC

def ask_backend(question, calendar_context, include_past=False, history=None):
    """Route LLM calls to the configured backend."""
    if get_backend() == "gemini":
        return ask_gemini(question, calendar_context, history=history)
    return ask_llm(question, calendar_context, include_past, history=history)

# ── Scheduled notifications ──

def _send_notification(title, body, digest_name="notification"):
    """Send an Apprise notification with error handling."""
    result = apobj.notify(title=title, body=body)
    if not result:
        print(f"Warning: {digest_name} notification failed to send.")

def _fetch_digest_events(start, end, label_filter=None):
    """Fetch events for digest notifications, optionally filtering by label."""
    all_events = []
    with _cal_lock:
        cals_snapshot = list(CALENDARS)
    for label, url in cals_snapshot:
        if label_filter and label not in label_filter:
            continue
        cal = fetch_events(url)
        if cal:
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

# ── Discord bot ──

def _keep_model_alive():
    """Ping Ollama to keep the model loaded in GPU memory."""
    try:
        requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": OLLAMA_MODEL, "messages": [], "keep_alive": "10m"},
            timeout=5,
        )
    except requests.exceptions.ConnectionError:
        print("[KeepAlive] Ollama offline — model will be unloaded")
    except requests.exceptions.Timeout:
        print("[KeepAlive] Timeout — model may be unloading")
    except Exception as e:
        print(f"[KeepAlive] Error: {e}")

def _make_async(fn):
    """Wrap a sync function for use with APScheduler's AsyncIOScheduler."""
    async def wrapper():
        await asyncio.to_thread(fn)
    wrapper.__name__ = fn.__name__
    return wrapper

_async_keep_model_alive = _make_async(_keep_model_alive)
_async_send_work_update = _make_async(send_work_update)
_async_send_weekend_update = _make_async(send_weekend_update)
_async_cleanup_conv_history = _make_async(_cleanup_conv_history)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

_scheduler_started = False
_tree_synced = False
_scheduler_lock = asyncio.Lock()
_ready_at = None
_demo_real_calendars = None  # saved real calendars when demo mode is active

def _configure_scheduler():
    """Create and configure the APScheduler instance with digest + keep-alive jobs."""
    scheduler = AsyncIOScheduler(timezone=TZ)
    if _weeknight:
        days, h, m = _weeknight
        scheduler.add_job(_async_send_work_update, "cron", day_of_week=days, hour=h, minute=m)
        print(f"  Weeknight digest: {days} at {h:02d}:{m:02d}")
    if _weekend:
        days, h, m = _weekend
        scheduler.add_job(_async_send_weekend_update, "cron", day_of_week=days, hour=h, minute=m)
        print(f"  Weekend preview: {days} at {h:02d}:{m:02d}")
    if get_backend() == "ollama":
        scheduler.add_job(_async_keep_model_alive, "interval", minutes=_KEEP_ALIVE_INTERVAL_MIN)
    # Periodic cleanup of stale conversation history (every 10 minutes)
    scheduler.add_job(_async_cleanup_conv_history, "interval", minutes=10)
    return scheduler

# ── Shared command handlers (used by both prefix and slash commands) ──

_HELP_TEXT = (
    "**Scout Report** — Calendar Assistant\n\n"
    "**Commands**\n"
    "`.help` — Show this help message\n"
    "`.cal` — List connected calendars\n"
    "`.llm` — Show/switch LLM backend\n"
    "`.ignore` — Manage events hidden from the AI entirely\n"
    "`.infoevent` — Manage events visible to AI but marked as informational\n"
    "`.demo` / `.demo off` — Toggle demo calendars\n\n"
    "_Run `.ignore` or `.infoevent` with no arguments to see sub-commands._\n\n"
    "**Or just ask a question** — no command needed!\n"
    "• *Am I free Tuesday afternoon?*\n"
    "• *What's on my calendar this weekend?*\n"
    "• *Recap last week's meetings*"
)

async def _handle_help(reply):
    await reply(_HELP_TEXT)

async def _handle_cal(reply):
    with _cal_lock:
        labels = [label for label, _ in CALENDARS]
    lines = [f"Connected calendars ({len(labels)}):"]
    lines.extend(f"{i}. **{label}**" for i, label in enumerate(labels, start=1))
    await reply("\n".join(lines))

async def _handle_llm_show(reply):
    current = get_backend()
    cur_gmodel = get_gemini_model()
    ollama_marker = " <-- **Current**" if current == "ollama" else ""
    gemini_rows = []
    model_info = [
        ("gemini-2.5-flash-lite", "15 RPM, ~200ms TTFT"),
        ("gemini-2.5-flash",      "10 RPM, ~300ms TTFT"),
    ]
    for m, info in model_info:
        marker = " <-- **Current**" if (current == "gemini" and cur_gmodel == m) else ""
        gemini_rows.append(f"   • `{m}` — {info}{marker}")
    await reply(
        f"1. **Ollama** — model: `{OLLAMA_MODEL}`{ollama_marker}\n"
        f"2. **Gemini** models:\n"
        + "\n".join(gemini_rows) + "\n\n"
        "Switch with: `.llm o`, `.llm fl`, `.llm gf`"
    )

_LLM_SWITCH_MAP = {
    "g":          ("gemini", None),
    "gemini":     ("gemini", None),
    "o":          ("ollama", None),
    "ollama":     ("ollama", None),
    "1":          ("ollama", None),
    "2":          ("gemini", None),
    "fl":         ("gemini", "gemini-2.5-flash-lite"),
    "flash-lite": ("gemini", "gemini-2.5-flash-lite"),
    "gf":         ("gemini", "gemini-2.5-flash"),
    "flash":      ("gemini", "gemini-2.5-flash"),
}

async def _handle_llm_switch(reply, choice, user_id, channel_id, is_dm, user_name="unknown"):
    entry = _LLM_SWITCH_MAP.get(choice.lower())
    if not entry:
        await reply(
            "Invalid choice. Use `.llm fl` (flash-lite), "
            "`.llm gf` (flash), or `.llm o` (ollama)"
        )
        return
    target_backend, target_model = entry
    try:
        set_backend(target_backend)
        if target_model:
            set_gemini_model(target_model)
        hist_key = (user_id if is_dm else channel_id, user_id)
        with _conv_lock:
            _conv_history.pop(hist_key, None)
        backend = get_backend()
        label = f"{backend} ({get_gemini_model()})" if backend == "gemini" else backend
        await reply(f"Switched to **{label}**")
        print(f"[Backend] Switched to {label} by {user_name}")
    except (ValueError, RuntimeError) as e:
        await reply(f"Failed: {e}")

async def _handle_demo(reply, action, user_name="unknown"):
    global _demo_real_calendars

    if action == "off" or (action == "" and _demo_real_calendars is not None):
        if _demo_real_calendars is not None:
            with _cal_lock:
                CALENDARS.clear()
                CALENDARS.extend(_demo_real_calendars)
            _demo_real_calendars = None
            _resize_executor()
            _future_ctx_cache["ts"] = 0
            _past_ctx_cache["ts"] = 0
            await reply("Demo mode **off** — restored real calendars.")
            print("[Demo] Disabled — restored real calendars")
        else:
            await reply("Demo mode is not active.")
        return

    from demo.calendars import (
        generate_work_calendar, generate_personal_calendar,
        generate_family_calendar, calendar_stats,
    )
    if _demo_real_calendars is None:
        _demo_real_calendars = list(CALENDARS)

    work_cal = generate_work_calendar()
    personal_cal = generate_personal_calendar()
    family_cal = generate_family_calendar()

    _demo_work_url = "__demo_work__"
    _demo_personal_url = "__demo_personal__"
    _demo_family_url = "__demo_family__"
    with _cal_lock:
        _cal_cache[_demo_work_url] = (work_cal, time.time())
        _cal_cache[_demo_personal_url] = (personal_cal, time.time())
        _cal_cache[_demo_family_url] = (family_cal, time.time())
        CALENDARS.clear()
        CALENDARS.append(("Work", _demo_work_url))
        CALENDARS.append(("Personal", _demo_personal_url))
        CALENDARS.append(("Family", _demo_family_url))
        _cal_labels[_demo_work_url] = "Work"
        _cal_labels[_demo_personal_url] = "Personal"
        _cal_labels[_demo_family_url] = "Family"

    _resize_executor()
    _future_ctx_cache["ts"] = 0
    _past_ctx_cache["ts"] = 0

    w_stats = calendar_stats(work_cal)
    p_stats = calendar_stats(personal_cal)
    f_stats = calendar_stats(family_cal)
    total = w_stats['total_events'] + p_stats['total_events'] + f_stats['total_events']
    await reply(
        f"**Demo mode ON** — using 90-day benchmark calendars\n"
        f"📋 Work: {w_stats['total_events']} events\n"
        f"📋 Personal: {p_stats['total_events']} events\n"
        f"📋 Family: {f_stats['total_events']} events\n"
        f"📊 Total: {total} events (30 days of history + 60 days of future)\n\n"
        f"Your real calendars are saved. Use `.demo off` to restore them."
    )
    print(f"[Demo] Enabled — Work: {w_stats['total_events']}, Personal: {p_stats['total_events']}, Family: {f_stats['total_events']} ({total} total)")

async def _handle_filter_command(reply, args_text, target_list, label, cmd,
                                invalidate_past=False, list_description=None,
                                hist_chan=None, user_id=None):
    """Generic handler for filter commands (.ignore / .infoevent).

    Args:
        reply: Async callable to send a response message.
        args_text: Raw argument string after the command name.
        target_list: The mutable filter list to operate on (e.g. IGNORED_EVENTS).
        label: Display name for the filter (e.g. "Ignore", "Info-event").
        cmd: Command name without dot prefix (e.g. "ignore", "infoevent").
        invalidate_past: If True, also invalidate the past-context cache on changes.
        list_description: Optional extra description shown when listing entries.
        hist_chan: Channel/user key for conversation history lookup (for 'last').
        user_id: User ID for conversation history lookup (for 'last').
    """
    args = args_text.strip()

    def _invalidate_caches():
        _future_ctx_cache["ts"] = 0
        if invalidate_past:
            _past_ctx_cache["ts"] = 0

    if not args:
        lines = [f"**{label} list** ({len(target_list)} entries):"]
        if target_list:
            for entry in target_list:
                lines.append(f"  • `{entry}`")
        else:
            lines.append("  *(empty)*")
        if list_description:
            lines.append(f"\n{list_description}")
        lines.append(f"Use `.{cmd} <event>` to add, `.{cmd} remove <event>` to remove, `.{cmd} remove all` to clear.")
        await reply("\n".join(lines))
        return

    if args.lower() == "remove all":
        removed = _remove_all_filter(target_list)
        _invalidate_caches()
        if removed:
            await reply(f"Removed {len(removed)} {label.lower()} entr{'y' if len(removed) == 1 else 'ies'}: {', '.join(f'`{r}`' for r in removed)}")
        else:
            await reply(f"No {label.lower()} entries to remove.")
        return

    if args.lower().startswith("remove ") or args.lower() == "remove":
        raw = args[len("remove "):].strip()
        if not raw:
            await reply(f"Usage: `.{cmd} remove <event>` or `.{cmd} remove <event1>, <event2>`")
            return
        names = [n.strip() for n in raw.split(",") if n.strip()]
        removed, not_found = _remove_from_filter(target_list, names)
        _invalidate_caches()
        parts = []
        if removed:
            parts.append(f"Removed: {', '.join(f'`{r}`' for r in removed)}")
        if not_found:
            parts.append(f"Not found: {', '.join(f'`{n}`' for n in not_found)}")
        await reply(" | ".join(parts) if parts else "Nothing to remove.")
        return

    if args.lower() == "last":
        last_reply = ""
        if hist_chan is not None and user_id is not None:
            history = _get_history(hist_chan, user_id)
            if history:
                last_reply = history[-1][1]
        if not last_reply:
            await reply(f"No previous bot reply found. Use `.{cmd} <event name>` to add events directly.")
            return
        events = _extract_events_from_reply(last_reply)
        if not events:
            await reply(f"Couldn't extract event names from the last reply. Use `.{cmd} <event name>` directly.")
            return
        added = _add_to_filter(target_list, events)
        _invalidate_caches()
        if added:
            await reply(f"Added {len(added)} event(s) to {label.lower()} list: {', '.join(f'`{a}`' for a in added)}")
        else:
            await reply(f"All found events are already in the {label.lower()} list.")
        return

    names = [n.strip() for n in args.split(",") if n.strip()]
    added = _add_to_filter(target_list, names)
    _invalidate_caches()
    if added:
        await reply(f"Added {len(added)} event(s) to {label.lower()} list: {', '.join(f'`{a}`' for a in added)}")
    else:
        await reply(f"All provided events are already in the {label.lower()} list (or names were empty).")

async def _handle_ignore(reply, args_text, hist_chan=None, user_id=None):
    """Handle .ignore command — add/remove/list/clear the ignore filter."""
    await _handle_filter_command(
        reply, args_text, IGNORED_EVENTS, "Ignore", "ignore",
        invalidate_past=True, hist_chan=hist_chan, user_id=user_id,
    )

async def _handle_infoevent(reply, args_text, hist_chan=None, user_id=None):
    """Handle .infoevent command — add/remove/list/clear the info-event filter."""
    await _handle_filter_command(
        reply, args_text, INFO_EVENTS, "Info-event", "infoevent",
        list_description="Info events are shown to the AI but marked as informational.",
        hist_chan=hist_chan, user_id=user_id,
    )

# ── Slash commands ──

@tree.command(name="help", description="Show available commands and tips")
async def slash_help(interaction: discord.Interaction):
    if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
        await interaction.response.send_message("You are not authorized.", ephemeral=True)
        return
    await _handle_help(interaction.response.send_message)

@tree.command(name="cal", description="List connected calendars")
async def slash_cal(interaction: discord.Interaction):
    if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
        await interaction.response.send_message("You are not authorized.", ephemeral=True)
        return
    await _handle_cal(interaction.response.send_message)

@tree.command(name="llm", description="Show or switch LLM backend")
@app_commands.describe(choice="Backend: o (ollama), fl (flash-lite), gf (flash)")
async def slash_llm(interaction: discord.Interaction, choice: str = None):
    if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
        await interaction.response.send_message("You are not authorized.", ephemeral=True)
        return
    if choice is None:
        await _handle_llm_show(interaction.response.send_message)
    else:
        is_dm = interaction.guild is None
        await _handle_llm_switch(
            interaction.response.send_message, choice,
            interaction.user.id, interaction.channel_id, is_dm,
            str(interaction.user),
        )

@tree.command(name="demo", description="Toggle demo calendars (synthetic data)")
@app_commands.describe(action="on or off (default: toggle)")
async def slash_demo(interaction: discord.Interaction, action: str = ""):
    if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
        await interaction.response.send_message("You are not authorized.", ephemeral=True)
        return
    await _handle_demo(interaction.response.send_message, action.lower(), str(interaction.user))

@tree.command(name="ignore", description="Add events to ignore list (hidden from AI)")
@app_commands.describe(events="Event name(s), comma-separated. Use 'last' for last reply, 'clear' to reset, or blank to list.")
async def slash_ignore(interaction: discord.Interaction, events: str = ""):
    if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
        await interaction.response.send_message("You are not authorized.", ephemeral=True)
        return
    is_dm = interaction.guild is None
    hist_chan = interaction.user.id if is_dm else interaction.channel_id
    await _handle_ignore(interaction.response.send_message, events, hist_chan, interaction.user.id)

@tree.command(name="infoevent", description="Mark events as info-only (shown but tagged as informational)")
@app_commands.describe(events="Event name(s), comma-separated. Use 'last' for last reply, 'remove all' to reset, or blank to list.")
async def slash_infoevent(interaction: discord.Interaction, events: str = ""):
    if DISCORD_ALLOWED_USERS and interaction.user.id not in DISCORD_ALLOWED_USERS:
        await interaction.response.send_message("You are not authorized.", ephemeral=True)
        return
    is_dm = interaction.guild is None
    hist_chan = interaction.user.id if is_dm else interaction.channel_id
    await _handle_infoevent(interaction.response.send_message, events, hist_chan, interaction.user.id)

@client.event
async def on_ready():
    global _scheduler_started, _tree_synced, _ready_at
    _ready_at = datetime.now(TZ)
    print(f"Discord bot logged in as {client.user}")
    if DISCORD_ALLOWED_USERS:
        print(f"  Allowed users: {DISCORD_ALLOWED_USERS}")
    async with _scheduler_lock:
        if not _scheduler_started:
            scheduler = _configure_scheduler()
            scheduler.start()
            _scheduler_started = True
            print("Scheduler started.")
        if not _tree_synced:
            try:
                await tree.sync()
                _tree_synced = True
                print("Slash commands synced.")
            except Exception as e:
                print(f"Warning: Failed to sync slash commands: {e}")

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

    # Normalize smart quotes/punctuation that mobile keyboards may inject
    question = question.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")

    # Strip wrapping quotes that mobile keyboards inject after . (e.g., ."llm" → .llm)
    # Only triggered when a quote immediately follows ., so commands with unquoted names are unaffected.
    if len(question) >= 2 and question[0] == '.' and question[1] in '"\'':
        q = question[1]  # the opening quote character
        rest = question[2:]
        # Remove only the matching closing quote (if present) to preserve intent
        question = '.' + (rest[:-1] if rest.endswith(q) else rest)

    # .help command — show available commands and tips
    if question.lower().startswith(".help"):
        await _handle_help(message.reply)
        return

    # .llm command — show current LLM backend options / switch backend
    if question.lower().startswith(".llm"):
        parts = question.split(maxsplit=1)
        if len(parts) == 1:
            await _handle_llm_show(message.reply)
        else:
            await _handle_llm_switch(
                message.reply, parts[1].strip(),
                message.author.id, message.channel.id, is_dm,
                str(message.author),
            )
        return

    # .cal command — list connected calendars
    if question.lower().startswith(".cal"):
        await _handle_cal(message.reply)
        return

    # .demo command — switch to demo calendars (ignores real env calendars)
    if question.lower().startswith(".demo"):
        parts = question.split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        await _handle_demo(message.reply, arg, str(message.author))
        return

    # .ignore command — add events to the ignore filter
    if question.lower().startswith(".ignore"):
        hist_chan = message.author.id if is_dm else message.channel.id
        await _handle_ignore(message.reply, question[7:].strip(), hist_chan, message.author.id)
        return

    # .infoevent command — add events to the info-event filter
    if question.lower().startswith(".infoevent"):
        hist_chan = message.author.id if is_dm else message.channel.id
        await _handle_infoevent(message.reply, question[10:].strip(), hist_chan, message.author.id)
        return

    # Natural-language shortcuts: "add X to ignore list" / "mark X as info event"
    _nl_ignore_m = _NL_IGNORE_RE.match(question)
    if _nl_ignore_m:
        hist_chan = message.author.id if is_dm else message.channel.id
        await _handle_ignore(message.reply, _nl_ignore_m.group(1).strip(), hist_chan, message.author.id)
        return

    _nl_infoevent_m = _NL_INFOEVENT_RE.match(question)
    if _nl_infoevent_m:
        hist_chan = message.author.id if is_dm else message.channel.id
        await _handle_infoevent(message.reply, _nl_infoevent_m.group(1).strip(), hist_chan, message.author.id)
        return

    # Use author ID as channel key for DMs (DM channel IDs can change)
    hist_chan = message.author.id if is_dm else message.channel.id

    # Per-user rate limiting — prevent single user from exhausting LLM API quota
    if _check_rate_limit(message.author.id):
        await message.reply(f"⏳ Please wait a few seconds between questions.")
        return

    try:
        async with message.channel.typing():
            # Classify whether the question needs past events (skip if history disabled)
            include_past = False
            if HISTORY_DAYS > 0:
                classification = await asyncio.to_thread(classify_question, question)
                include_past = classification == "past"
                if include_past:
                    print(f"[Chat] Including past events (classification: {classification})")
            # Retrieve conversation history for follow-ups
            history = _get_history(hist_chan, message.author.id)
            if history:
                print(f"[Conv] Including {len(history)} previous exchange(s)")
            # Run blocking calendar fetch + LLM call in a thread
            calendar_context = await asyncio.to_thread(build_context, include_past)
            answer = await asyncio.to_thread(ask_backend, question, calendar_context, include_past, history)

        # Store exchange for future follow-ups (without signature)
        _store_exchange(hist_chan, message.author.id, question, answer)

        print(f"[Chat] Reply ({len(answer)} chars): {answer[:100]}...")
        # Sign the reply with the active model
        backend = get_backend()
        model_name = get_gemini_model() if backend == 'gemini' else OLLAMA_MODEL
        signature = f"\n*— {model_name}*"
        # Discord has a 2000 char limit; fit truncation + signature if needed
        if len(answer) + len(signature) > _DISCORD_MSG_LIMIT:
            trunc = "\n…(truncated)"
            overhead = len(trunc) + len(signature)
            if overhead <= _DISCORD_MSG_LIMIT:
                answer = answer[:_DISCORD_MSG_LIMIT - overhead] + trunc + signature
            else:
                answer = answer[:_DISCORD_MSG_LIMIT]
        else:
            answer += signature

        await message.reply(answer)
    except Exception as e:
        print(f"[Chat] Error handling message from {message.author}: {e}")
        await message.reply("❌ Something went wrong processing your question. Please try again.")

# ── Graceful shutdown ──

def _shutdown():
    """Clean up resources on exit."""
    print("[Shutdown] Cleaning up...")
    _cal_executor.shutdown(wait=False)

atexit.register(_shutdown)

# ── Main entry point ──

async def run_scheduler_only():
    """Run just the scheduler when no Discord token is configured."""
    loop = asyncio.get_running_loop()
    scheduler = _configure_scheduler()
    scheduler.start()
    print("Scout Report Started. Scheduler running (no Discord chat).")

    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    scheduler.shutdown(wait=False)
    print("[Shutdown] Scheduler stopped.")

if __name__ == "__main__":
    if DISCORD_BOT_TOKEN:
        print("Starting Discord bot + scheduler...")
        client.run(DISCORD_BOT_TOKEN)
    else:
        print("DISCORD_BOT_TOKEN not set — running scheduler only (no chat).")
        asyncio.run(run_scheduler_only())