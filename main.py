import os
import re
import asyncio
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse, quote
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

# ── LLM Backend selection ──
_llm_backend = os.getenv("LLM_BACKEND", "gemini").lower()
if _llm_backend not in ("ollama", "gemini"):
    raise RuntimeError(f"LLM_BACKEND must be 'ollama' or 'gemini', got: {_llm_backend!r}")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e4b")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
_gemini_client = None
_gemini_api_key = os.getenv("GEMINI_API_KEY")

def _get_gemini_client():
    """Lazy-init Gemini client on first use."""
    global _gemini_client
    if _gemini_client is None:
        if not _gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Gemini backend")
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

if _llm_backend == "gemini":
    _get_gemini_client()  # validate at startup
    print(f"LLM backend: Gemini ({GEMINI_MODEL})")
else:
    print(f"LLM backend: Ollama ({OLLAMA_MODEL} at {OLLAMA_URL})")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

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
try:
    _weeknight = _parse_schedule(os.getenv("WEEKNIGHT_SCHEDULE", "sun,mon,tue,wed,thu 20:00"), "sun,mon,tue,wed,thu", "20:00")
except (ValueError, IndexError) as e:
    raise RuntimeError(f"Invalid WEEKNIGHT_SCHEDULE: {os.getenv('WEEKNIGHT_SCHEDULE')!r} — {e}") from e

# Weekend preview: Fri-Sun events (default: Thu 16:00)
try:
    _weekend = _parse_schedule(os.getenv("WEEKEND_SCHEDULE", "thu 16:00"), "thu", "16:00")
except (ValueError, IndexError) as e:
    raise RuntimeError(f"Invalid WEEKEND_SCHEDULE: {os.getenv('WEEKEND_SCHEDULE')!r} — {e}") from e

# ── Signal notification via signal-cli REST API ──
# Apprise signal:// plugin — requires a running signal-cli REST API instance.
# See SETUP.md §3 and docker-compose.yaml for the optional sidecar service.
_signal_api_url = os.getenv("SIGNAL_CLI_REST_API_URL", "").rstrip("/")
_signal_from = os.getenv("SIGNAL_FROM_NUMBER", "").strip()
_signal_to = os.getenv("SIGNAL_TO_NUMBER", "").strip()
_signal_configured = bool(_signal_api_url and _signal_from and _signal_to)

def _build_signal_apprise_url(api_url, from_num, to_num):
    """Construct an Apprise signal:// URL from signal-cli REST API config.

    Uses signals:// for HTTPS endpoints, signal:// for HTTP.
    Phone numbers and group IDs are percent-encoded so special characters
    ('+', '=', '/') are safe in the URL path.
    """
    parsed = urlparse(api_url)
    scheme = "signals" if parsed.scheme == "https" else "signal"
    host = parsed.hostname
    port = parsed.port
    netloc = f"{host}:{port}" if port else host
    from_encoded = quote(from_num, safe="")
    to_encoded = quote(to_num, safe="")
    return f"{scheme}://{netloc}/{from_encoded}/{to_encoded}"

def _mask_number(num):
    """Redact middle digits of a phone number or group ID for safe logging."""
    if len(num) <= 8:
        return num[:2] + "***"
    return num[:6] + "***" + num[-4:]

# Setup Apprise (only required when scheduled digests are enabled)
_schedules_enabled = _weeknight is not None or _weekend is not None
_apprise_url = os.getenv("APPRISE_URL")
if _schedules_enabled:
    if not _apprise_url and not _signal_configured:
        raise RuntimeError(
            "A notification target is required when scheduled digests are enabled. "
            "Set APPRISE_URL (Discord webhook, Telegram, etc.) and/or configure Signal "
            "(SIGNAL_CLI_REST_API_URL + SIGNAL_FROM_NUMBER + SIGNAL_TO_NUMBER). "
            "To disable digests: WEEKNIGHT_SCHEDULE=off WEEKEND_SCHEDULE=off."
        )
    apobj = apprise.Apprise()
    if _apprise_url:
        apobj.add(_apprise_url)
    if _signal_configured:
        _signal_apprise_url = _build_signal_apprise_url(_signal_api_url, _signal_from, _signal_to)
        apobj.add(_signal_apprise_url)
        _signal_api_host = urlparse(_signal_api_url).netloc
        print(f"Signal notifications enabled: {_mask_number(_signal_from)} → {_mask_number(_signal_to)} via {_signal_api_host}")
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
_MAX_OUTPUT_TOKENS = 512
_GEMINI_MAX_RETRIES = 3
_GEMINI_RETRY_WAIT_BASE = 15  # seconds
_KEEP_ALIVE_INTERVAL_MIN = 4
_DISCORD_MSG_LIMIT = 1900

from collections import deque
# Keyed by (channel_id, user_id) → deque of (question, answer, timestamp)
_conv_history: dict[tuple[int, int], deque] = {}

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

# Reverse lookup: url -> label (for safe logging without exposing URL tokens)
_cal_labels = {url: label for label, url in CALENDARS}

def fetch_events(url):
    """Fetch and parse calendar events, with caching."""
    now = time.time()
    cached = _cal_cache.get(url)
    if cached and (now - cached[1]) < _CAL_CACHE_TTL:
        return cached[0]
    cal_label = _cal_labels.get(url, "unknown")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        cal = Calendar.from_ical(response.text)
        _cal_cache[url] = (cal, now)
        return cal
    except requests.RequestException as e:
        print(f"Error fetching calendar '{cal_label}': {type(e).__name__}: {e.response.status_code if hasattr(e, 'response') and e.response else 'connection failed'}")
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

# Classification prompt (module-level constant, used by classify_question Tier 2)
_CLASSIFY_PROMPT = (
    "Classify this question as needing 'past' calendar events, "
    "'future' calendar events, or 'both'. Respond with ONLY one word: "
    "past, future, or both."
)

def classify_question(question):
    """Classify whether a question needs past events, future events, or both.
    
    Returns 'past' or 'future'.
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
        if get_backend() == "gemini":
            resp = _get_gemini_client().models.generate_content(
                model=GEMINI_MODEL,
                contents=question,
                config={
                    "system_instruction": _CLASSIFY_PROMPT,
                    "temperature": 0,
                    "max_output_tokens": 16,
                    "thinking_config": {"thinking_budget": 0},
                },
            )
            answer = (resp.text or "").strip().lower()
        else:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": _CLASSIFY_PROMPT},
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
        error_msg = str(e).lower()
        if "429" in error_msg or "resource_exhausted" in error_msg:
            print(f"Classification skipped (rate limited) — defaulting to future context")
        else:
            print(f"Classification fallback failed: {e}")
        return "future"  # safe default

# ── Conversation history helpers ──

def _get_history(channel_id, user_id):
    """Return non-stale conversation history as [(question, answer), ...]."""
    key = (channel_id, user_id)
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
    """Store a Q&A exchange, truncating the answer for token budget."""
    key = (channel_id, user_id)
    if key not in _conv_history:
        _conv_history[key] = deque(maxlen=CONV_HISTORY_TURNS)
    truncated = answer[:_CONV_ANSWER_TRUNC] + "…" if len(answer) > _CONV_ANSWER_TRUNC else answer
    _conv_history[key].append((question, truncated, time.time()))

def _estimate_tokens(text):
    """Rough token estimate: ~4 chars per token."""
    return len(text) // 4

# ── LLM ──

def ask_llm(question, calendar_context, include_past=False, history=None):
    """Send a question + calendar context to Ollama and return the response."""
    num_ctx = _OLLAMA_CTX_WITH_PAST if include_past else _OLLAMA_CTX_BASE
    system_prompt = f"{SYSTEM_PROMPT}\n\nCALENDAR DATA:\n{calendar_context}"

    # Build multi-turn messages: [system, ...history pairs..., current user]
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        # Token budget check — only bump num_ctx if history doesn't fit in base.
        # Avoiding unnecessary bumps keeps Ollama's KV cache hot (~5x faster prompt eval).
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
    except requests.exceptions.ConnectionError:
        return "🔌 LLM is offline — Ollama may not be running or is unreachable."
    except requests.exceptions.Timeout:
        return "⏳ LLM timed out — the model may still be loading. Try again in a minute."
    except Exception as e:
        print(f"LLM error: {e}")
        return "❌ Something went wrong with the LLM. Check server logs."

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
    last_err = None
    for attempt in range(_GEMINI_MAX_RETRIES):
        try:
            resp = _get_gemini_client().models.generate_content(
                model=GEMINI_MODEL,
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
            return "I couldn't generate a response. Try rephrasing your question."
        except Exception as e:
            last_err = e
            error_msg = str(e).lower()
            if "429" in error_msg or "resource_exhausted" in error_msg:
                if attempt < _GEMINI_MAX_RETRIES - 1:
                    wait = (attempt + 1) * _GEMINI_RETRY_WAIT_BASE
                    print(f"Gemini rate limited, retrying in {wait}s (attempt {attempt + 1}/3)")
                    time.sleep(wait)
                    continue
                return "⏳ Gemini rate limit reached — try again in a minute."
            if "api key" in error_msg or "401" in error_msg or "403" in error_msg:
                return "🔑 Gemini API key is invalid or expired. Check your GEMINI_API_KEY."
            print(f"Gemini error: {e}")
            return "❌ Something went wrong with Gemini. Check server logs."
    print(f"Gemini error after retries: {last_err}")
    return "❌ Something went wrong with Gemini. Check server logs."

def ask_backend(question, calendar_context, include_past=False, history=None):
    """Route LLM calls to the configured backend."""
    if get_backend() == "gemini":
        return ask_gemini(question, calendar_context, history=history)
    return ask_llm(question, calendar_context, include_past, history=history)

# ── Scheduled notifications ──

def send_work_update():
    """Triggered weeknights for tomorrow's work schedule."""
    try:
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
    except Exception as e:
        print(f"Error in weeknight digest: {e}")

def send_weekend_update():
    """Triggered Thursday for weekend context."""
    try:
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
                scheduler.add_job(_async_send_work_update, "cron", day_of_week=days, hour=h, minute=m)
                print(f"  Weeknight digest: {days} at {h:02d}:{m:02d}")
            if _weekend:
                days, h, m = _weekend
                scheduler.add_job(_async_send_weekend_update, "cron", day_of_week=days, hour=h, minute=m)
                print(f"  Weekend preview: {days} at {h:02d}:{m:02d}")
            # Keep the Ollama model loaded in memory
            if get_backend() == "ollama":
                scheduler.add_job(_async_keep_model_alive, "interval", minutes=_KEEP_ALIVE_INTERVAL_MIN)
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

    # Normalize smart quotes/punctuation that mobile keyboards may inject
    question = question.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")

    # !backend command — show or switch LLM backend
    _backend_map = {"1": "ollama", "2": "gemini"}
    if question.lower().startswith("!backend"):
        parts = question.split(maxsplit=1)
        if len(parts) == 1:
            current = get_backend()
            await message.reply(
                f"1. **Ollama** - model: `{OLLAMA_MODEL}`{' <-- Current' if current == 'ollama' else ''}\n"
                f"2. **Gemini** - model: `{GEMINI_MODEL}`{' <-- Current' if current == 'gemini' else ''}\n\n"
                "Switch with: `!backend 1` or `!backend 2`"
            )
        else:
            target = _backend_map.get(parts[1].strip(), parts[1].strip().lower())
            try:
                set_backend(target)
                # Clear conversation history on backend switch
                hist_key = (message.author.id if is_dm else message.channel.id, message.author.id)
                _conv_history.pop(hist_key, None)
                await message.reply(f"Switched to **{get_backend()}**")
                print(f"[Backend] Switched to {get_backend()} by {message.author}")
            except (ValueError, RuntimeError) as e:
                await message.reply(f"Failed: {e}")
        return

    print(f"[Chat] {message.author}: {question}")
    # Use author ID as channel key for DMs (DM channel IDs can change)
    hist_chan = message.author.id if is_dm else message.channel.id
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

        # Store exchange for future follow-ups
        _store_exchange(hist_chan, message.author.id, question, answer)

        print(f"[Chat] Reply ({len(answer)} chars): {answer[:100]}...")
        # Discord has a 2000 char limit
        if len(answer) > _DISCORD_MSG_LIMIT:
            answer = answer[:_DISCORD_MSG_LIMIT] + "\n…(truncated)"

        await message.reply(answer)
    except Exception as e:
        print(f"[Chat] Error handling message from {message.author}: {e}")
        await message.reply("❌ Something went wrong processing your question. Please try again.")

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
    print("Scout Report Started. Scheduler running (no Discord chat).")
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