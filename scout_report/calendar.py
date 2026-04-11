import time
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timedelta
from typing import NamedTuple, Optional
from icalendar import Calendar
import recurring_ical_events

from scout_report.config import (
    TZ, CALENDARS, WORK_LABELS, IGNORED_EVENTS,
    CONTEXT_DAYS, HISTORY_DAYS, HISTORY_CACHE_TTL,
    _work_cals, _personal_cals,
)

# ── Event type ──

class Event(NamedTuple):
    dt: datetime
    summary: str
    duration_min: Optional[int]
    all_day: bool = False

# ── Calendar cache ──
_cal_cache = {}       # url -> (Calendar, timestamp)
_CAL_CACHE_TTL = int(__import__('os').getenv("CACHE_TTL", "3600"))  # seconds

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
