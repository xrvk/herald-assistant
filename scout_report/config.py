import os
import re
from collections import deque
from urllib.parse import urlparse, quote
from zoneinfo import ZoneInfo

# ── Timezone ──
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

# ── Discord configuration ──
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
# See SETUP.md §2b and docker-compose.yaml for the optional sidecar service.
_signal_api_url = os.getenv("SIGNAL_CLI_REST_API_URL", "").rstrip("/")
_signal_from = os.getenv("SIGNAL_FROM_NUMBER", "").strip()
_signal_to = os.getenv("SIGNAL_TO_NUMBER", "").strip()
_signal_configured = bool(_signal_api_url and _signal_from and _signal_to)

# Signal interactive chat (requires signal-cli REST API + account registration)
SIGNAL_CHAT = os.getenv("SIGNAL_CHAT", "").strip().lower() in ("1", "true", "on", "yes")
_signal_chat_enabled = bool(SIGNAL_CHAT and _signal_api_url and _signal_from)

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
_SIGNAL_MSG_LIMIT = 4000

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

# ── Question classification patterns ──

# Patterns that indicate the user is asking about past events
PAST_PATTERNS = re.compile(
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
FUTURE_PATTERNS = re.compile(
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

# Classification prompt (used by classify_question Tier 2)
CLASSIFY_PROMPT = (
    "Classify this question as needing 'past' calendar events, "
    "'future' calendar events, or 'both'. Respond with ONLY one word: "
    "past, future, or both."
)
