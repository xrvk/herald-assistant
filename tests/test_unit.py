"""
Unit tests for Scout Report bot internals.

Run: pytest tests/test_unit.py -v
"""

import os
import sys
import re
import time
import random
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pytest

# ── Patch environment before importing main ──
# Provide minimal env vars so main.py doesn't crash on import.
_TEST_ENV = {
    "DISCORD_BOT_TOKEN": "fake-token-for-testing",
    "DISCORD_CHANNEL_ID": "1234567890",
    "LLM_BACKEND": "ollama",
    "OLLAMA_URL": "http://localhost:11434",
    "OLLAMA_MODEL": "test-model",
    "GEMINI_API_KEY": "",
    "ICLOUD_URL": "https://fake.cal/feed.ics",
    "ICLOUD_LABEL": "TestCal",
    "OUTLOOK_URL": "",
    "GOOGLE_URL": "",
    "IGNORED_EVENTS": "lunch,canceled,declined",
    "WEEKNIGHT_SCHEDULE": "off",
    "WEEKEND_SCHEDULE": "off",
    "HISTORY_DAYS": "10",
    "CONTEXT_DAYS": "7",
    "TZ": "America/Los_Angeles",
    "APPRISE_URL": "",
    "CONV_HISTORY_TURNS": "3",
    "CONV_HISTORY_TTL": "1800",
}


@pytest.fixture(autouse=True, scope="session")
def patch_env():
    """Patch env vars before any main.py import."""
    with patch.dict(os.environ, _TEST_ENV, clear=False):
        yield


# ── Standalone normalization helpers (mirror main.py logic) ──

def _normalize_event(s: str) -> str:
    """Lowercase, strip quotes and non-alphanumeric chars (keep spaces) for fuzzy matching."""
    s = s.strip().strip('"').strip("'").lower()
    return re.sub(r"[^\w\s]", "", s, flags=re.UNICODE).strip()

def _parse_event_list(raw: str) -> list[str]:
    """Parse a comma-separated event list, normalizing each entry."""
    if not raw.strip():
        return []
    return [n for e in raw.split(",") if (n := _normalize_event(e))]


# ── Helpers for building fake iCal components ──

def _make_vevent(summary, dt_start, dt_end=None, all_day=False):
    """Build a minimal iCal VEVENT component mock."""
    comp = MagicMock()
    comp.name = "VEVENT"

    if all_day:
        dt_val = MagicMock()
        dt_val.dt = dt_start if isinstance(dt_start, date) else dt_start.date()
    else:
        dt_val = MagicMock()
        dt_val.dt = dt_start

    def getter(key, default=None):
        mapping = {
            "dtstart": dt_val,
            "summary": summary,
            "dtend": None,
            "duration": None,
        }
        if dt_end:
            end_mock = MagicMock()
            end_mock.dt = dt_end
            mapping["dtend"] = end_mock
        return mapping.get(key, default)

    comp.get = getter
    return comp


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Ignored Events Filter
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestIgnoredEvents:
    """Verify IGNORED_EVENTS filtering in get_upcoming_events."""

    def test_ignored_events_parsed(self):
        """IGNORED_EVENTS env var is parsed into a list of normalized substrings."""
        with patch.dict(os.environ, _TEST_ENV):
            raw = os.environ["IGNORED_EVENTS"]
            parsed = _parse_event_list(raw)
            assert "lunch" in parsed
            assert "canceled" in parsed
            assert "declined" in parsed

    def test_ignored_event_substring_match(self):
        """Events with ignored substrings are filtered out (using normalized matching)."""
        ignored = ["lunch", "canceled", "declined"]
        test_events = [
            "Team Lunch Meeting",
            "Standup",
            "Canceled: 1:1 with Bob",
            "Sprint Planning",
            "Declined - All Hands",
        ]
        kept = [e for e in test_events if not any(ig in _normalize_event(e) for ig in ignored)]
        assert kept == ["Standup", "Sprint Planning"]

    def test_empty_ignore_list_keeps_all(self):
        """No IGNORED_EVENTS means nothing is filtered."""
        ignored = []
        test_events = ["Lunch", "Standup", "Canceled meeting"]
        kept = [e for e in test_events if not (ignored and any(ig in e.lower() for ig in ignored))]
        assert len(kept) == 3

    def test_ignored_events_audit(self):
        """Audit: print which events would be ignored by the configured list."""
        raw = _TEST_ENV["IGNORED_EVENTS"]
        ignored = _parse_event_list(raw)

        # Simulate a realistic calendar day
        sample_events = [
            "Morning Standup",
            "Lunch",
            "1:1 with Manager",
            "Canceled: Design Review",
            "Sprint Planning",
            "Declined - Team Outing",
            "Allergy Appointment",
            "Coffee Chat",
        ]

        filtered_out = [e for e in sample_events if any(ig in _normalize_event(e) for ig in ignored)]
        kept = [e for e in sample_events if not any(ig in _normalize_event(e) for ig in ignored)]

        print(f"\n  IGNORED_EVENTS config: {ignored}")
        print(f"  Sample events filtered OUT: {filtered_out}")
        print(f"  Sample events KEPT: {kept}")

        # The three we expect to be ignored
        assert "Lunch" in filtered_out
        assert "Canceled: Design Review" in filtered_out
        assert "Declined - Team Outing" in filtered_out
        # The rest should be kept
        assert "Morning Standup" in kept
        assert "Sprint Planning" in kept

    def test_get_upcoming_events_filters_ignored(self):
        """Run demo work calendar through main.get_upcoming_events — ignored events are dropped."""
        from tests.demo_calendars import generate_work_ics, _monday_of_week
        import main

        tz = ZoneInfo("America/Los_Angeles")
        mon = _monday_of_week(datetime.now(tz))
        work_cal = generate_work_ics()

        # Query full week
        week_end = (mon + timedelta(days=6)).replace(hour=23, minute=59, second=59)

        original_ignored = main.IGNORED_EVENTS
        try:
            main.IGNORED_EVENTS = ["lunch", "canceled", "declined"]
            result = main.get_upcoming_events(work_cal, mon, week_end)
        finally:
            main.IGNORED_EVENTS = original_ignored

        summaries = [e.summary for e in result]
        print(f"\n  Work events after ignore filter ({len(summaries)}): {summaries}")

        # These should be filtered out
        assert not any("Lunch" in s for s in summaries), "Lunch events should be filtered"
        assert not any("Canceled" in s for s in summaries), "Canceled events should be filtered"
        assert not any("Declined" in s for s in summaries), "Declined events should be filtered"
        # These should survive
        assert any("Daily Standup" in s for s in summaries)
        assert any("Sprint Planning" in s for s in summaries)
        assert any("1:1 with Manager" in s for s in summaries)

    def test_personal_calendar_filters_ignored(self):
        """Run demo personal calendar through main.get_upcoming_events — drop/pick-up filtered."""
        from tests.demo_calendars import generate_personal_ics, _monday_of_week
        import main

        tz = ZoneInfo("America/Los_Angeles")
        mon = _monday_of_week(datetime.now(tz))
        personal_cal = generate_personal_ics()

        week_end = (mon + timedelta(days=6)).replace(hour=23, minute=59, second=59)

        original_ignored = main.IGNORED_EVENTS
        try:
            # Use the real .env ignore list patterns that match demo events
            main.IGNORED_EVENTS = ["drop off kids", "pick up kids", "lunch", "canceled", "declined"]
            result = main.get_upcoming_events(personal_cal, mon, week_end)
        finally:
            main.IGNORED_EVENTS = original_ignored

        summaries = [e.summary for e in result]
        print(f"\n  Personal events after ignore filter ({len(summaries)}): {summaries}")

        assert not any("Drop off kids" in s for s in summaries), "Drop-off should be filtered"
        assert not any("Pick up kids" in s for s in summaries), "Pick-up should be filtered"
        # Family events should survive
        assert any("Date Night" in s for s in summaries)
        assert any("Soccer Practice" in s for s in summaries)
        assert any("Family Brunch" in s for s in summaries)

    def test_demo_calendars_unfiltered_counts(self):
        """Verify demo calendars have expected event counts before filtering."""
        from tests.demo_calendars import generate_work_ics, generate_personal_ics, calendar_stats

        work_stats = calendar_stats(generate_work_ics())
        personal_stats = calendar_stats(generate_personal_ics())

        print(f"\n  Work calendar: {work_stats['total_events']} events")
        print(f"  Personal calendar: {personal_stats['total_events']} events")
        print(f"  Work events: {work_stats['summaries']}")
        print(f"  Personal events: {personal_stats['summaries']}")

        # Work: 5 days × ~4-5 events + 2 weekend = ~22-25
        assert work_stats["total_events"] >= 20, f"Expected 20+ work events, got {work_stats['total_events']}"
        # Personal: 5 × drop-off + 5 × pick-up + ~10 family = ~20
        assert personal_stats["total_events"] >= 18, f"Expected 18+ personal events, got {personal_stats['total_events']}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Question Classification
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestClassification:
    """Test Tier 1 regex-based question classification (no LLM needed)."""

    # Reproduce the regex patterns from main.py
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

    @pytest.mark.parametrize("question", [
        "What happened yesterday?",
        "What did I have last Monday?",
        "How was my week?",
        "Recap this past week",
        "How many meetings did I have 3 days ago?",
    ])
    def test_past_questions(self, question):
        assert self.PAST_PATTERNS.search(question), f"Should classify as past: {question}"
        assert not self.FUTURE_PATTERNS.search(question), f"Should NOT match future: {question}"

    @pytest.mark.parametrize("question", [
        "What's on my schedule tomorrow?",
        "Am I free next Tuesday?",
        "Do I have anything this weekend?",
        "What's coming up this week?",
        "Any meetings tonight?",
    ])
    def test_future_questions(self, question):
        assert self.FUTURE_PATTERNS.search(question), f"Should classify as future: {question}"
        assert not self.PAST_PATTERNS.search(question), f"Should NOT match past: {question}"

    def test_random_weekday_question(self):
        """Generate a random weekday question and verify it classifies as future."""
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        day = random.choice(days)
        question = f"What's on my schedule next {day}?"
        assert self.FUTURE_PATTERNS.search(question)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Event Formatting
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFormatting:
    """Test event formatting for notifications and context."""

    def test_format_duration_minutes(self):
        assert _format_duration(30) == "(30m)"
        assert _format_duration(45) == "(45m)"

    def test_format_duration_hours(self):
        assert _format_duration(60) == "(1h)"
        assert _format_duration(120) == "(2h)"

    def test_format_duration_mixed(self):
        assert _format_duration(90) == "(1h 30m)"
        assert _format_duration(150) == "(2h 30m)"

    def test_format_duration_none(self):
        assert _format_duration(None) == ""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. Conversation History
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestConversationHistory:
    """Test conversation history storage and retrieval logic."""

    def test_store_and_retrieve(self):
        from collections import deque

        conv = {}
        key = (123, 456)
        conv[key] = deque(maxlen=3)
        conv[key].append(("q1", "a1", time.time()))
        conv[key].append(("q2", "a2", time.time()))

        valid = [(q, a) for q, a, ts in conv[key] if (time.time() - ts) < 1800]
        assert len(valid) == 2

    def test_stale_entries_filtered(self):
        from collections import deque

        conv = {}
        key = (123, 456)
        conv[key] = deque(maxlen=3)
        old_ts = time.time() - 3600  # 1 hour ago
        conv[key].append(("old_q", "old_a", old_ts))
        conv[key].append(("new_q", "new_a", time.time()))

        valid = [(q, a) for q, a, ts in conv[key] if (time.time() - ts) < 1800]
        assert len(valid) == 1
        assert valid[0][0] == "new_q"

    def test_answer_truncation(self):
        max_trunc = 500
        long_answer = "x" * 1000
        truncated = long_answer[:max_trunc] + "…" if len(long_answer) > max_trunc else long_answer
        assert len(truncated) == 501  # 500 + "…"
        assert truncated.endswith("…")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. Discord Message Limits
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDiscordLimits:
    """Test message truncation logic."""

    MSG_LIMIT = 1900

    def test_short_message_not_truncated(self):
        answer = "Here are your events for tomorrow."
        signature = "\n*— Gemini (gemini-2.5-flash)*"
        result = answer + signature
        assert len(result) <= self.MSG_LIMIT

    def test_long_message_truncated(self):
        answer = "x" * 2000
        signature = "\n*— Gemini (gemini-2.5-flash)*"
        trunc = "\n…(truncated)"
        overhead = len(trunc) + len(signature)
        if len(answer) + len(signature) > self.MSG_LIMIT:
            answer = answer[:self.MSG_LIMIT - overhead] + trunc + signature
        assert len(answer) <= self.MSG_LIMIT

    def test_smart_quote_normalization(self):
        """Mobile keyboards inject smart quotes around commands."""
        q = "\u201c.llm\u201d"
        q = q.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
        assert q == '".llm"'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  6. Backend Logic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBackendLogic:
    """Test backend switching logic (no actual LLM calls)."""

    def test_switch_map_coverage(self):
        switch_map = {
            "g": "gemini", "gemini": "gemini",
            "o": "ollama", "ollama": "ollama",
            "1": "ollama", "2": "gemini",
        }
        assert switch_map["g"] == "gemini"
        assert switch_map["o"] == "ollama"
        assert switch_map["1"] == "ollama"
        assert switch_map["2"] == "gemini"
        assert switch_map.get("invalid") is None

    def test_token_estimate(self):
        """Token estimation: ~4 chars per token."""
        text = "Hello, world!"  # 13 chars
        estimate = len(text) // 4
        assert estimate == 3


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  7. Schedule Parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestScheduleParsing:
    """Test schedule configuration parsing."""

    @staticmethod
    def _parse_schedule(raw, default_days, default_time):
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

    def test_full_format(self):
        result = self._parse_schedule("mon,tue,wed 09:00", "mon", "08:00")
        assert result == ("mon,tue,wed", 9, 0)

    def test_time_only(self):
        result = self._parse_schedule("14:30", "mon,fri", "08:00")
        assert result == ("mon,fri", 14, 30)

    def test_off(self):
        for val in ("off", "false", "disabled", "none", ""):
            assert self._parse_schedule(val, "mon", "08:00") is None


# ── Event normalization ──

class TestNormalizeEvent:
    """Test _normalize_event helper for fuzzy event matching."""

    def test_lowercase(self):
        assert _normalize_event("Mom Babysit") == "mom babysit"

    def test_strip_quotes(self):
        assert _normalize_event('"Mom Babysit"') == "mom babysit"
        assert _normalize_event("'Mom Babysit'") == "mom babysit"

    def test_strip_special_chars(self):
        assert _normalize_event("Mom's Appointment") == "moms appointment"
        assert _normalize_event("Canceled: Design Review") == "canceled design review"
        assert _normalize_event("Declined - Team Outing") == "declined  team outing"

    def test_whitespace_trim(self):
        assert _normalize_event("  lunch  ") == "lunch"

    def test_empty(self):
        assert _normalize_event("") == ""
        assert _normalize_event("  ") == ""


# ── NON_BLOCKING_EVENTS parsing ──

class TestNonBlockingEvents:
    """Test NON_BLOCKING_EVENTS env var parsing (same format as IGNORED_EVENTS)."""

    def test_empty(self):
        assert _parse_event_list("") == []
        assert _parse_event_list("   ") == []

    def test_single_entry(self):
        assert _parse_event_list("Mom Babysit") == ["mom babysit"]

    def test_multiple_entries(self):
        assert _parse_event_list("Mom Babysit,Dog Walker") == ["mom babysit", "dog walker"]

    def test_whitespace_stripping(self):
        assert _parse_event_list("  Mom Babysit , Dog Walker  ") == ["mom babysit", "dog walker"]

    def test_empty_entries_skipped(self):
        assert _parse_event_list("Mom Babysit,,Dog Walker,") == ["mom babysit", "dog walker"]

    def test_quoted_entries(self):
        """Quotes around individual entries are stripped."""
        assert _parse_event_list('"Mom Babysit","Dog Walker"') == ["mom babysit", "dog walker"]
        assert _parse_event_list("'Mom Babysit','Dog Walker'") == ["mom babysit", "dog walker"]

    def test_special_chars_stripped(self):
        """Apostrophes, colons, etc. are removed during normalization."""
        assert _parse_event_list("Mom's Appt") == ["moms appt"]
        assert _parse_event_list("Canceled: Meeting") == ["canceled meeting"]


class TestNonBlockingSystemPrompt:
    """Test that NON_BLOCKING_EVENTS are injected into the system prompt."""

    def test_prompt_appended(self):
        non_blocking = ["mom babysit", "dog walker"]
        base_prompt = "You are a helpful assistant."
        nb_list = ", ".join(f'"{e}"' for e in non_blocking)
        result = base_prompt + (
            "\n\nNON-BLOCKING EVENTS: " + nb_list + ". "
            "These events are informational and do NOT block the user's schedule. "
            "When determining availability, ignore them."
        )
        assert "NON-BLOCKING EVENTS" in result
        assert '"mom babysit"' in result
        assert '"dog walker"' in result
        assert "do NOT block" in result

    def test_no_injection_when_empty(self):
        non_blocking = []
        base_prompt = "You are a helpful assistant."
        result = base_prompt
        if non_blocking:
            result += "\n\nNON-BLOCKING EVENTS"
        assert result == base_prompt


# ── Standalone helper to avoid import side effects ──

def _format_duration(minutes):
    if minutes is None:
        return ""
    if minutes < 60:
        return f"({minutes}m)"
    hours, mins = divmod(minutes, 60)
    if mins == 0:
        return f"({hours}h)"
    return f"({hours}h {mins}m)"
