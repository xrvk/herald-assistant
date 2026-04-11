import asyncio
import requests
from datetime import datetime, timedelta
from urllib.parse import urlparse
import apprise

from scout_report.config import (
    TZ, CALENDARS, WORK_LABELS, OLLAMA_URL, OLLAMA_MODEL,
    _weeknight, _weekend, _signal_configured, _signal_api_url, _signal_from, _signal_to,
    _build_signal_apprise_url, _mask_number, _KEEP_ALIVE_INTERVAL_MIN,
    get_backend,
)
from scout_report.calendar import (
    fetch_events, get_upcoming_events, format_events_for_notification,
)

# ── Apprise setup (only required when scheduled digests are enabled) ──
_schedules_enabled = _weeknight is not None or _weekend is not None
_apprise_url = __import__('os').getenv("APPRISE_URL")
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

# ── Scheduled digest jobs ──

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

# ── Ollama keep-alive ──

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

# ── Async wrappers (for APScheduler async jobs) ──

async def async_keep_model_alive():
    await asyncio.to_thread(_keep_model_alive)

async def async_send_work_update():
    await asyncio.to_thread(send_work_update)

async def async_send_weekend_update():
    await asyncio.to_thread(send_weekend_update)
