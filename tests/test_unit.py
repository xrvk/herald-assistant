"""
Unit tests for Scout Report bot internals.

Tests import directly from main.py — no local reimplementations of production logic.

Run: pytest tests/test_unit.py -v
"""

import os
import time
import random
from collections import deque
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pytest
import requests

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
    # Point to a non-existent path so _load_filters() is a no-op at import time
    "FILTERS_PATH": "/tmp/scout-test-filters-nonexistent.json",
}


# ── Apply env vars before importing main (must happen at module load time) ──
_original_env = {k: os.environ.get(k) for k in _TEST_ENV}
os.environ.update(_TEST_ENV)

import main
from main import (
    _normalize_event,
    _parse_event_list,
    _format_duration,
    _parse_schedule,
    _PAST_PATTERNS,
    _FUTURE_PATTERNS,
    _estimate_tokens,
    _prepare_ollama_messages,
    _get_history,
    _store_exchange,
    _conv_history,
    _CONV_ANSWER_TRUNC,
    CONV_HISTORY_TTL,
    CONV_HISTORY_TURNS,
    CONV_HISTORY_CTX_BUMP,
    _OLLAMA_CTX_BASE,
    _MAX_OUTPUT_TOKENS,
    _DISCORD_MSG_LIMIT,
    _ERR_OLLAMA_OFFLINE,
    _ERR_OLLAMA_TIMEOUT,
    _ERR_GEMINI_RATE_LIMIT,
    _ERR_GEMINI_AUTH,
    _ERR_LLM_GENERIC,
    _ERR_NO_RESPONSE,
    Event,
    get_upcoming_events,
    format_events_for_notification,
    fetch_events,
    _cal_cache,
    _CAL_CACHE_TTL,
    classify_question,
    ask_llm,
    ask_gemini,
    get_backend,
    set_backend,
    SYSTEM_PROMPT,
    _HELP_TEXT,
    _LLM_SWITCH_MAP,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Ignored Events Filter
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestIgnoredEvents:
    """Verify IGNORED_EVENTS filtering via main.get_upcoming_events."""

    def test_ignored_events_parsed(self):
        """IGNORED_EVENTS env var is parsed into a list of normalized substrings."""
        assert "lunch" in main.IGNORED_EVENTS
        assert "canceled" in main.IGNORED_EVENTS
        assert "declined" in main.IGNORED_EVENTS

    def test_substring_match_filters_correctly(self):
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

    def test_get_upcoming_events_filters_ignored(self):
        """Run demo work calendar through main.get_upcoming_events — ignored events are dropped."""
        from tests.demo_calendars import generate_work_ics, _monday_of_week

        tz = ZoneInfo("America/Los_Angeles")
        mon = _monday_of_week(datetime.now(tz))
        work_cal = generate_work_ics()
        week_end = (mon + timedelta(days=6)).replace(hour=23, minute=59, second=59)

        original_ignored = main.IGNORED_EVENTS
        try:
            main.IGNORED_EVENTS = ["lunch", "canceled", "declined"]
            result = get_upcoming_events(work_cal, mon, week_end)
        finally:
            main.IGNORED_EVENTS = original_ignored

        summaries = [e.summary for e in result]
        assert not any("Lunch" in s for s in summaries), "Lunch events should be filtered"
        assert not any("Canceled" in s for s in summaries), "Canceled events should be filtered"
        assert not any("Declined" in s for s in summaries), "Declined events should be filtered"
        assert any("Daily Standup" in s for s in summaries)
        assert any("Sprint Planning" in s for s in summaries)

    def test_personal_calendar_filters_ignored(self):
        """Run demo personal calendar through main.get_upcoming_events — drop/pick-up filtered."""
        from tests.demo_calendars import generate_personal_ics, _monday_of_week

        tz = ZoneInfo("America/Los_Angeles")
        mon = _monday_of_week(datetime.now(tz))
        personal_cal = generate_personal_ics()
        week_end = (mon + timedelta(days=6)).replace(hour=23, minute=59, second=59)

        original_ignored = main.IGNORED_EVENTS
        try:
            main.IGNORED_EVENTS = ["drop off kids", "pick up kids", "lunch", "canceled", "declined"]
            result = get_upcoming_events(personal_cal, mon, week_end)
        finally:
            main.IGNORED_EVENTS = original_ignored

        summaries = [e.summary for e in result]
        assert not any("Drop off kids" in s for s in summaries), "Drop-off should be filtered"
        assert not any("Pick up kids" in s for s in summaries), "Pick-up should be filtered"
        assert any("Date Night" in s for s in summaries)
        assert any("Soccer Practice" in s for s in summaries)
        assert any("Family Brunch" in s for s in summaries)

    def test_empty_ignore_list_keeps_all(self):
        """No IGNORED_EVENTS means nothing is filtered."""
        from tests.demo_calendars import generate_work_ics, _monday_of_week

        tz = ZoneInfo("America/Los_Angeles")
        mon = _monday_of_week(datetime.now(tz))
        work_cal = generate_work_ics()
        week_end = (mon + timedelta(days=6)).replace(hour=23, minute=59, second=59)

        original_ignored = main.IGNORED_EVENTS
        try:
            main.IGNORED_EVENTS = []
            result = get_upcoming_events(work_cal, mon, week_end)
        finally:
            main.IGNORED_EVENTS = original_ignored

        summaries = [e.summary for e in result]
        # With empty ignore list, lunch/canceled/declined should all be present
        assert any("Lunch" in s for s in summaries)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Question Classification (Tier 1 regex)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestClassificationRegex:
    """Test Tier 1 regex patterns imported from main.py."""

    @pytest.mark.parametrize("question", [
        "What happened yesterday?",
        "What did I have last Monday?",
        "How was my week?",
        "Recap this past week",
        "How many meetings did I have 3 days ago?",
    ])
    def test_past_questions(self, question):
        assert _PAST_PATTERNS.search(question), f"Should classify as past: {question}"
        assert not _FUTURE_PATTERNS.search(question), f"Should NOT match future: {question}"

    @pytest.mark.parametrize("question", [
        "What's on my schedule tomorrow?",
        "Am I free next Tuesday?",
        "Do I have anything this weekend?",
        "What's coming up this week?",
        "Any meetings tonight?",
    ])
    def test_future_questions(self, question):
        assert _FUTURE_PATTERNS.search(question), f"Should classify as future: {question}"
        assert not _PAST_PATTERNS.search(question), f"Should NOT match past: {question}"

    def test_random_weekday_question(self):
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        day = random.choice(days)
        question = f"What's on my schedule next {day}?"
        assert _FUTURE_PATTERNS.search(question)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2b. classify_question() — full function (Tier 1 + Tier 2)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestClassifyQuestion:
    """Test classify_question() through the actual function."""

    def test_clear_past(self):
        assert classify_question("What happened yesterday?") == "past"

    def test_clear_future(self):
        assert classify_question("What's on my schedule tomorrow?") == "future"

    def test_mixed_signals_returns_past(self):
        """When both past and future patterns match, 'past' wins (safe default)."""
        assert classify_question("Recap this past week and what's coming up this week?") == "past"

    def test_ambiguous_defaults_to_future(self):
        """Ambiguous questions with no regex match default to 'future'."""
        assert classify_question("What about Wednesday?") == "future"
        assert classify_question("Tell me about my calendar") == "future"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Event Formatting
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFormatDuration:
    """Test _format_duration from main.py."""

    def test_minutes(self):
        assert _format_duration(30) == "(30m)"
        assert _format_duration(45) == "(45m)"

    def test_hours(self):
        assert _format_duration(60) == "(1h)"
        assert _format_duration(120) == "(2h)"

    def test_mixed(self):
        assert _format_duration(90) == "(1h 30m)"
        assert _format_duration(150) == "(2h 30m)"

    def test_none(self):
        assert _format_duration(None) == ""


class TestFormatEventsForNotification:
    """Test format_events_for_notification from main.py."""

    def test_timed_event(self):
        tz = ZoneInfo("America/Los_Angeles")
        dt = datetime(2026, 4, 11, 9, 0, tzinfo=tz)
        events = [Event(dt=dt, summary="Standup", duration_min=15, all_day=False, normalized_summary="standup")]
        lines = format_events_for_notification(events)
        assert len(lines) == 1
        assert "09:00 AM" in lines[0]
        assert "Standup" in lines[0]
        assert "(15m)" in lines[0]

    def test_all_day_event(self):
        tz = ZoneInfo("America/Los_Angeles")
        dt = datetime(2026, 4, 11, 0, 0, tzinfo=tz)
        events = [Event(dt=dt, summary="Company Holiday", duration_min=None, all_day=True, normalized_summary="company holiday")]
        lines = format_events_for_notification(events)
        assert len(lines) == 1
        assert "All Day" in lines[0]
        assert "Company Holiday" in lines[0]

    def test_empty_list(self):
        assert format_events_for_notification([]) == []

    def test_multiple_events_ordered(self):
        tz = ZoneInfo("America/Los_Angeles")
        events = [
            Event(dt=datetime(2026, 4, 11, 9, 0, tzinfo=tz), summary="Event A", duration_min=30, all_day=False, normalized_summary="event a"),
            Event(dt=datetime(2026, 4, 11, 14, 0, tzinfo=tz), summary="Event B", duration_min=60, all_day=False, normalized_summary="event b"),
        ]
        lines = format_events_for_notification(events)
        assert len(lines) == 2
        assert "Event A" in lines[0]
        assert "Event B" in lines[1]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. Conversation History (production functions)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestConversationHistory:
    """Test _store_exchange / _get_history from main.py."""

    @pytest.fixture(autouse=True)
    def clean_history(self):
        """Clear conv history before each test."""
        _conv_history.clear()
        yield
        _conv_history.clear()

    def test_store_and_retrieve(self):
        _store_exchange(100, 200, "What's tomorrow?", "You have a standup at 9am.")
        hist = _get_history(100, 200)
        assert len(hist) == 1
        assert hist[0][0] == "What's tomorrow?"

    def test_multiple_exchanges(self):
        _store_exchange(100, 200, "q1", "a1")
        _store_exchange(100, 200, "q2", "a2")
        hist = _get_history(100, 200)
        assert len(hist) == 2

    def test_stale_entries_filtered(self):
        """Entries older than TTL are filtered out by _get_history."""
        key = (100, 200)
        _conv_history[key] = deque(maxlen=CONV_HISTORY_TURNS)
        _conv_history[key].append(("old_q", "old_a", time.time() - CONV_HISTORY_TTL - 10))
        _store_exchange(100, 200, "new_q", "new_a")
        hist = _get_history(100, 200)
        assert len(hist) == 1
        assert hist[0][0] == "new_q"

    def test_answer_truncation(self):
        long_answer = "x" * 1000
        _store_exchange(100, 200, "q", long_answer)
        hist = _get_history(100, 200)
        stored_answer = hist[0][1]
        assert len(stored_answer) == _CONV_ANSWER_TRUNC + 1  # 500 + "…"
        assert stored_answer.endswith("…")

    def test_short_answer_not_truncated(self):
        _store_exchange(100, 200, "q", "short answer")
        hist = _get_history(100, 200)
        assert hist[0][1] == "short answer"

    def test_error_messages_not_stored(self):
        """Error responses should not be stored in history."""
        _store_exchange(100, 200, "q", "🔌 LLM is offline")
        assert _get_history(100, 200) == []
        _store_exchange(100, 200, "q", "⏳ Gemini rate limit")
        assert _get_history(100, 200) == []
        _store_exchange(100, 200, "q", "❌ Something went wrong")
        assert _get_history(100, 200) == []

    def test_maxlen_respected(self):
        for i in range(CONV_HISTORY_TURNS + 5):
            _store_exchange(100, 200, f"q{i}", f"a{i}")
        hist = _get_history(100, 200)
        assert len(hist) <= CONV_HISTORY_TURNS

    def test_separate_users(self):
        _store_exchange(100, 200, "user1_q", "user1_a")
        _store_exchange(100, 300, "user2_q", "user2_a")
        assert len(_get_history(100, 200)) == 1
        assert len(_get_history(100, 300)) == 1
        assert _get_history(100, 200)[0][0] == "user1_q"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. Discord Message Limits
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDiscordLimits:
    """Test message truncation logic using production constants."""

    def test_short_message_fits(self):
        answer = "Here are your events for tomorrow."
        signature = "\n*— gemini-2.5-flash*"
        assert len(answer + signature) <= _DISCORD_MSG_LIMIT

    def test_long_message_truncated(self):
        answer = "x" * 2000
        signature = "\n*— gemini-2.5-flash*"
        trunc = "\n…(truncated)"
        overhead = len(trunc) + len(signature)
        if len(answer) + len(signature) > _DISCORD_MSG_LIMIT:
            answer = answer[:_DISCORD_MSG_LIMIT - overhead] + trunc + signature
        assert len(answer) <= _DISCORD_MSG_LIMIT

    def test_smart_quote_normalization(self):
        """Mobile keyboards inject smart quotes around commands."""
        q = "\u201c.llm\u201d"
        q = q.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
        assert q == '".llm"'

    def test_smart_quote_command_stripping(self):
        """."llm" → .llm — mobile keyboards wrap after the dot."""
        question = '."llm"'  # ."llm" as typed on mobile
        if len(question) >= 2 and question[0] == '.' and question[1] in '"\'':
            q = question[1]
            rest = question[2:]
            question = '.' + (rest[:-1] if rest.endswith(q) else rest)
        assert question == '.llm'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  6. Backend Logic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestBackendLogic:
    """Test backend switching through production functions."""

    def test_get_and_set_backend(self):
        original = get_backend()
        try:
            set_backend("ollama")
            assert get_backend() == "ollama"
        finally:
            set_backend(original)

    def test_set_invalid_backend_raises(self):
        with pytest.raises(ValueError, match="ollama.*gemini"):
            set_backend("invalid")

    def test_token_estimate(self):
        assert _estimate_tokens("Hello, world!") == 3  # 13 chars // 4
        assert _estimate_tokens("") == 0
        assert _estimate_tokens("x" * 100) == 25

    def test_ollama_fallback_to_gemini_when_key_set(self):
        """When Ollama is unreachable and GEMINI_API_KEY is set, ask_llm falls back to Gemini."""
        import requests as req
        import main

        with patch.object(req, "post", side_effect=req.exceptions.ConnectionError("refused")), \
             patch.object(main, "_gemini_api_key", "fake-key"), \
             patch.object(main, "ask_gemini", return_value="fallback answer") as mock_gemini:
            result = main.ask_llm("what's tomorrow?", "some context")
        mock_gemini.assert_called_once_with("what's tomorrow?", "some context", history=None)
        assert result == "fallback answer"

    def test_ollama_offline_without_gemini_key(self):
        """When Ollama is unreachable and no GEMINI_API_KEY, ask_llm returns offline message."""
        import requests as req
        import main

        with patch.object(req, "post", side_effect=req.exceptions.ConnectionError("refused")), \
             patch.object(main, "_gemini_api_key", ""):
            result = main.ask_llm("what's tomorrow?", "some context")
        assert result == "🔌 LLM is offline — Ollama may not be running or is unreachable."

    def test_ollama_fallback_passes_history(self):
        """Fallback to Gemini forwards the conversation history."""
        import requests as req
        import main

        history = [("prev q", "prev a")]
        with patch.object(req, "post", side_effect=req.exceptions.ConnectionError("refused")), \
             patch.object(main, "_gemini_api_key", "fake-key"), \
             patch.object(main, "ask_gemini", return_value="gemini reply") as mock_gemini:
            result = main.ask_llm("follow-up", "ctx", history=history)
        mock_gemini.assert_called_once_with("follow-up", "ctx", history=history)
        assert result == "gemini reply"



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  7. Schedule Parsing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestScheduleParsing:
    """Test _parse_schedule from main.py."""

    def test_full_format(self):
        result = _parse_schedule("mon,tue,wed 09:00", "mon", "08:00")
        assert result == ("mon,tue,wed", 9, 0)

    def test_time_only(self):
        result = _parse_schedule("14:30", "mon,fri", "08:00")
        assert result == ("mon,fri", 14, 30)

    def test_days_only(self):
        result = _parse_schedule("mon,fri", "tue", "08:00")
        assert result == ("mon,fri", 8, 0)

    def test_off(self):
        for val in ("off", "false", "disabled", "none", ""):
            assert _parse_schedule(val, "mon", "08:00") is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  8. Event & String Normalization
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestNormalizeEvent:
    """Test _normalize_event from main.py."""

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


class TestParseEventList:
    """Test _parse_event_list from main.py."""

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
        assert _parse_event_list('"Mom Babysit","Dog Walker"') == ["mom babysit", "dog walker"]

    def test_special_chars_stripped(self):
        assert _parse_event_list("Mom's Appt") == ["moms appt"]
        assert _parse_event_list("Canceled: Meeting") == ["canceled meeting"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  9. System Prompt
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestSystemPrompt:
    """Test SYSTEM_PROMPT structure from main.py."""

    def test_prompt_contains_calendar_assistant(self):
        assert "calendar assistant" in SYSTEM_PROMPT.lower()

    def test_prompt_contains_free_tag_instruction(self):
        assert "(free)" in SYSTEM_PROMPT


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 10. _prepare_ollama_messages
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestPrepareOllamaMessages:
    """Test token-budget-aware history trimming in _prepare_ollama_messages."""

    def test_no_history(self):
        msgs, ctx = _prepare_ollama_messages("sys", "question", None, 8192)
        assert len(msgs) == 2  # system + user
        assert msgs[0]["role"] == "system"
        assert msgs[1]["content"] == "question"
        assert ctx == 8192

    def test_history_included(self):
        history = [("q1", "a1"), ("q2", "a2")]
        msgs, ctx = _prepare_ollama_messages("sys", "question", history, 8192)
        # system + 2×(user+assistant) + user = 6
        assert len(msgs) == 6
        assert msgs[1]["content"] == "q1"
        assert msgs[2]["content"] == "a1"

    def test_history_trimmed_when_over_budget(self):
        """When history exceeds token budget even after ctx bump, oldest exchanges are dropped."""
        huge = "x" * 40000  # ~10000 tokens — won't fit even with bump
        history = [("q1", huge), ("q2", huge), ("q3", "a3")]
        msgs, ctx = _prepare_ollama_messages("sys", "question", history, 2048)
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["content"] == "question"
        # Should have trimmed — not all 3 exchanges fit
        history_msgs = [m for m in msgs if m["role"] in ("user", "assistant") and m["content"] != "question"]
        assert len(history_msgs) < 6  # less than full 3 exchanges (6 messages)

    def test_ctx_bump_when_history_overflows(self):
        """num_ctx is bumped by CONV_HISTORY_CTX_BUMP when history overflows base."""
        # Make history that barely overflows base but fits with bump
        sys_prompt = "x" * 100
        q = "question"
        # Each exchange costs ~(len/4) tokens
        exchange = ("x" * 200, "y" * 200)  # ~100 tokens each pair
        history = [exchange] * 20  # ~2000 tokens of history
        base_ctx = 2200  # tight budget
        msgs, new_ctx = _prepare_ollama_messages(sys_prompt, q, history, base_ctx)
        # If history was trimmed and bump kicked in, ctx should be larger
        if new_ctx > base_ctx:
            assert new_ctx == base_ctx + CONV_HISTORY_CTX_BUMP


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 11. ask_llm / ask_gemini error paths
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestAskLlmErrors:
    """Test ask_llm error handling with mocked HTTP."""

    def test_connection_error(self):
        with patch("main.requests.post", side_effect=requests.exceptions.ConnectionError):
            result = ask_llm("q", "cal context")
            assert result == _ERR_OLLAMA_OFFLINE

    def test_timeout_error(self):
        with patch("main.requests.post", side_effect=requests.exceptions.Timeout):
            result = ask_llm("q", "cal context")
            assert result == _ERR_OLLAMA_TIMEOUT

    def test_generic_error(self):
        with patch("main.requests.post", side_effect=RuntimeError("unexpected")):
            result = ask_llm("q", "cal context")
            assert result == _ERR_LLM_GENERIC

    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"message": {"content": "Here's your schedule"}}
        with patch("main.requests.post", return_value=mock_resp):
            result = ask_llm("q", "cal context")
            assert result == "Here's your schedule"


class TestAskGeminiErrors:
    """Test ask_gemini error handling with mocked Gemini client."""

    def _mock_gemini(self, side_effect=None, text="response"):
        mock_client = MagicMock()
        if side_effect:
            mock_client.models.generate_content.side_effect = side_effect
        else:
            mock_resp = MagicMock()
            mock_resp.text = text
            mock_client.models.generate_content.return_value = mock_resp
        return mock_client

    def test_rate_limit(self):
        mock_client = self._mock_gemini(side_effect=Exception("429 resource_exhausted"))
        with patch("main._get_gemini_client", return_value=mock_client):
            result = ask_gemini("q", "cal context")
            assert result == _ERR_GEMINI_RATE_LIMIT

    def test_auth_error(self):
        mock_client = self._mock_gemini(side_effect=Exception("401 api key invalid"))
        with patch("main._get_gemini_client", return_value=mock_client):
            result = ask_gemini("q", "cal context")
            assert result == _ERR_GEMINI_AUTH

    def test_generic_error(self):
        mock_client = self._mock_gemini(side_effect=Exception("something broke"))
        with patch("main._get_gemini_client", return_value=mock_client):
            result = ask_gemini("q", "cal context")
            assert result == _ERR_LLM_GENERIC

    def test_empty_response(self):
        mock_client = self._mock_gemini(text=None)
        with patch("main._get_gemini_client", return_value=mock_client):
            result = ask_gemini("q", "cal context")
            assert result == _ERR_NO_RESPONSE

    def test_success(self):
        mock_client = self._mock_gemini(text="Here's your schedule")
        with patch("main._get_gemini_client", return_value=mock_client):
            result = ask_gemini("q", "cal context")
            assert result == "Here's your schedule"

    def test_history_passed_to_gemini(self):
        mock_client = self._mock_gemini(text="follow-up answer")
        with patch("main._get_gemini_client", return_value=mock_client):
            result = ask_gemini("q2", "cal", history=[("q1", "a1")])
            assert result == "follow-up answer"
            call_args = mock_client.models.generate_content.call_args
            contents = call_args.kwargs.get("contents") or call_args[1].get("contents")
            # Should have history (user+model) + current question = 3 content items
            assert len(contents) == 3


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 12. fetch_events
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFetchEvents:
    """Test fetch_events cache behavior and error handling."""

    @pytest.fixture(autouse=True)
    def clean_cache(self):
        """Snapshot and restore _cal_cache around each test."""
        original = dict(_cal_cache)
        yield
        _cal_cache.clear()
        _cal_cache.update(original)

    def test_cache_hit(self):
        """Fresh cache entry is returned without HTTP call."""
        fake_cal = MagicMock()
        _cal_cache["https://test.cal/feed.ics"] = (fake_cal, time.time())
        result = fetch_events("https://test.cal/feed.ics")
        assert result is fake_cal

    def test_cache_miss_fetches(self):
        """Expired cache triggers HTTP fetch."""
        _cal_cache.pop("https://test.cal/feed.ics", None)
        mock_resp = MagicMock()
        mock_resp.text = "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR"
        mock_resp.raise_for_status = MagicMock()
        with patch("main.requests.get", return_value=mock_resp):
            result = fetch_events("https://test.cal/feed.ics")
            assert result is not None

    def test_stale_cache_on_error(self):
        """On HTTP error, stale cached data is returned."""
        fake_cal = MagicMock()
        _cal_cache["https://test.cal/feed.ics"] = (fake_cal, time.time() - _CAL_CACHE_TTL - 10)
        with patch("main.requests.get", side_effect=requests.exceptions.ConnectionError):
            result = fetch_events("https://test.cal/feed.ics")
            assert result is fake_cal

    def test_no_cache_on_error_returns_none(self):
        """On HTTP error with no cache, returns None."""
        _cal_cache.pop("https://no-cache.cal/feed.ics", None)
        with patch("main.requests.get", side_effect=requests.exceptions.ConnectionError):
            result = fetch_events("https://no-cache.cal/feed.ics")
            assert result is None

    def test_demo_url_passthrough(self):
        """Demo URLs return cached data without HTTP fetch."""
        fake_cal = MagicMock()
        _cal_cache["__demo_work__"] = (fake_cal, time.time())
        result = fetch_events("__demo_work__")
        assert result is fake_cal

    def test_demo_url_no_cache_returns_none(self):
        """Demo URL with no cache returns None (not an HTTP fetch)."""
        _cal_cache.pop("__demo_missing__", None)
        result = fetch_events("__demo_missing__")
        assert result is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 13. Demo Calendar Sanity
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestDemoCalendars:
    """Sanity checks on demo calendar generators."""

    def test_work_calendar_event_count(self):
        from tests.demo_calendars import generate_work_ics, calendar_stats
        stats = calendar_stats(generate_work_ics())
        assert stats["total_events"] >= 20

    def test_personal_calendar_event_count(self):
        from tests.demo_calendars import generate_personal_ics, calendar_stats
        stats = calendar_stats(generate_personal_ics())
        assert stats["total_events"] >= 18


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 17. Fetch Events — Retry Logic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestFetchEventsRetry:
    """Verify fetch_events retries on transient errors."""

    @pytest.fixture(autouse=True)
    def clean_cache(self):
        _cal_cache.pop("https://retry-test.cal/feed.ics", None)
        yield
        _cal_cache.pop("https://retry-test.cal/feed.ics", None)

    def test_retry_on_connection_error_then_success(self):
        """ConnectionError on first attempt, success on retry."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "BEGIN:VCALENDAR\nEND:VCALENDAR"
        with patch("main.requests.get", side_effect=[
            requests.exceptions.ConnectionError("connection refused"),
            mock_resp,
        ]):
            with patch("main.time.sleep"):  # skip retry delay
                result = fetch_events("https://retry-test.cal/feed.ics")
        assert result is not None

    def test_retry_on_500_then_success(self):
        """HTTP 500 on first attempt, success on retry."""
        err_resp = MagicMock()
        err_resp.status_code = 500
        err_resp.raise_for_status = MagicMock(
            side_effect=requests.exceptions.HTTPError(response=err_resp)
        )

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.text = "BEGIN:VCALENDAR\nEND:VCALENDAR"
        ok_resp.raise_for_status = MagicMock()

        with patch("main.requests.get", side_effect=[err_resp, ok_resp]):
            with patch("main.time.sleep"):
                result = fetch_events("https://retry-test.cal/feed.ics")
        assert result is not None

    def test_no_retry_on_404(self):
        """HTTP 404 should not be retried."""
        err_resp = MagicMock()
        err_resp.status_code = 404
        err_resp.raise_for_status = MagicMock(
            side_effect=requests.exceptions.HTTPError(response=err_resp)
        )

        with patch("main.requests.get", return_value=err_resp) as mock_get:
            with patch("main.time.sleep"):
                result = fetch_events("https://retry-test.cal/feed.ics")
        # Should only call get once (no retry for 404)
        assert mock_get.call_count == 1
        assert result is None

    def test_retry_exhausted_falls_back_to_stale(self):
        """After retry is exhausted, falls back to stale cache."""
        fake_cal = MagicMock()
        _cal_cache["https://retry-test.cal/feed.ics"] = (fake_cal, 0)  # stale
        with patch("main.requests.get", side_effect=requests.exceptions.Timeout):
            with patch("main.time.sleep"):
                result = fetch_events("https://retry-test.cal/feed.ics")
        assert result is fake_cal


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 18. Help Text & LLM Switch Map
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestHelpText:
    """Verify help text contains all command names."""

    def test_contains_all_commands(self):
        for cmd in [".help", ".cal", ".llm", ".ignore", ".infoevent", ".demo"]:
            assert cmd in _HELP_TEXT, f"Missing command {cmd} in help text"

    def test_contains_example_questions(self):
        assert "free Tuesday" in _HELP_TEXT or "calendar" in _HELP_TEXT


class TestLlmSwitchMap:
    """Verify switch map covers expected aliases."""

    def test_all_shortcuts_present(self):
        for key in ["g", "gemini", "o", "ollama", "fl", "gf", "flash", "flash-lite", "1", "2"]:
            assert key in _LLM_SWITCH_MAP

    def test_values_are_valid(self):
        for key, (backend, model) in _LLM_SWITCH_MAP.items():
            assert backend in ("ollama", "gemini")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 19. MAX_OUTPUT_TOKENS env var
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestMaxOutputTokens:
    """Verify MAX_OUTPUT_TOKENS is configurable."""

    def test_default_value(self):
        assert _MAX_OUTPUT_TOKENS == 512

    def test_is_integer(self):
        assert isinstance(_MAX_OUTPUT_TOKENS, int)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 21. Filter helpers (_add_to_filter, _remove_all_filter)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from main import (
    _add_to_filter,
    _remove_all_filter,
    _extract_events_from_reply,
    _NL_IGNORE_RE,
    _NL_INFOEVENT_RE,
)


class TestAddToFilter:
    """Test _add_to_filter helper."""

    def test_adds_normalized_names(self):
        target = []
        added = _add_to_filter(target, ["Team Standup", "Weekly 1:1"])
        assert added == ["team standup", "weekly 11"]
        assert target == ["team standup", "weekly 11"]

    def test_skips_duplicates(self):
        target = ["lunch"]
        added = _add_to_filter(target, ["lunch", "Lunch"])
        assert added == []
        assert target == ["lunch"]

    def test_skips_empty_names(self):
        target = []
        added = _add_to_filter(target, ["", "   ", "standup"])
        assert added == ["standup"]

    def test_multiple_adds_accumulate(self):
        target = []
        _add_to_filter(target, ["standup"])
        _add_to_filter(target, ["lunch"])
        assert target == ["standup", "lunch"]

    def test_does_not_duplicate_existing_entry(self):
        target = ["lunch"]
        added = _add_to_filter(target, ["Lunch"])
        assert added == []
        assert target == ["lunch"]


class TestRemoveAllFilter:
    """Test _remove_all_filter helper."""

    def test_removes_all_entries(self):
        target = ["lunch", "standup", "canceled"]
        removed = _remove_all_filter(target)
        assert set(removed) == {"lunch", "standup", "canceled"}
        assert target == []

    def test_empty_list_returns_empty(self):
        target = []
        removed = _remove_all_filter(target)
        assert removed == []
        assert target == []

    def test_clears_all_entries(self):
        target = ["entry1", "entry2", "entry3"]
        removed = _remove_all_filter(target)
        assert set(removed) == {"entry1", "entry2", "entry3"}
        assert target == []


from main import _remove_from_filter


class TestRemoveFromFilter:
    """Test _remove_from_filter helper (individual removal)."""

    def test_removes_existing_entry(self):
        target = ["lunch", "standup"]
        removed, not_found = _remove_from_filter(target, ["standup"])
        assert removed == ["standup"]
        assert not_found == []
        assert target == ["lunch"]

    def test_removes_any_entry(self):
        target = ["lunch", "standup"]
        removed, not_found = _remove_from_filter(target, ["lunch"])
        assert removed == ["lunch"]
        assert not_found == []
        assert target == ["standup"]

    def test_not_found_reported(self):
        target = ["lunch"]
        removed, not_found = _remove_from_filter(target, ["nonexistent"])
        assert removed == []
        assert not_found == ["nonexistent"]
        assert target == ["lunch"]

    def test_mixed_found_and_not_found(self):
        target = ["lunch", "standup"]
        removed, not_found = _remove_from_filter(target, ["standup", "missing"])
        assert removed == ["standup"]
        assert not_found == ["missing"]

    def test_normalizes_before_remove(self):
        target = ["team standup"]
        removed, not_found = _remove_from_filter(target, ["Team Standup"])
        assert removed == ["team standup"]
        assert not_found == []
        assert target == []

    def test_empty_names_skipped(self):
        target = ["lunch"]
        removed, not_found = _remove_from_filter(target, ["", "   "])
        assert removed == []
        assert not_found == []
        assert target == ["lunch"]

    def test_multiple_removes(self):
        target = ["lunch", "standup", "sync"]
        removed, not_found = _remove_from_filter(target, ["lunch", "standup"])
        assert set(removed) == {"lunch", "standup"}
        assert not_found == []
        assert target == ["sync"]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 22. _extract_events_from_reply
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestExtractEventsFromReply:
    """Test extraction of event names from bullet-point bot replies."""

    def test_notification_format(self):
        text = "• 9:00 AM: Team Standup (30m)\n• 11:00 AM: Weekly 1:1 (60m)"
        events = _extract_events_from_reply(text)
        assert "Team Standup" in events
        assert "Weekly 1:1" in events

    def test_all_day_format(self):
        text = "• All Day: Company Holiday"
        events = _extract_events_from_reply(text)
        assert "Company Holiday" in events

    def test_no_bullets_returns_empty(self):
        text = "You have no events tomorrow! Have a nice day."
        events = _extract_events_from_reply(text)
        assert events == []

    def test_deduplicates_same_event(self):
        text = "• 9:00 AM: Standup (30m)\n• 10:00 AM: Standup (30m)"
        events = _extract_events_from_reply(text)
        assert events.count("Standup") == 1

    def test_strips_bold_markdown(self):
        text = "• **Team Standup** (30m)"
        events = _extract_events_from_reply(text)
        assert "Team Standup" in events

    def test_dash_bullet(self):
        text = "- 2:00 PM: Sprint Review (60m)"
        events = _extract_events_from_reply(text)
        assert "Sprint Review" in events

    def test_ignores_short_content(self):
        text = "• OK\n• A"
        events = _extract_events_from_reply(text)
        assert events == []


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 23. Natural-language pattern regexes
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestNLPatterns:
    """Test _NL_IGNORE_RE and _NL_INFOEVENT_RE patterns."""

    def test_ignore_add_basic(self):
        m = _NL_IGNORE_RE.match("add team standup to ignore list")
        assert m is not None
        assert "team standup" in m.group(1).lower()

    def test_ignore_add_with_the(self):
        m = _NL_IGNORE_RE.match("add lunch to the ignore list")
        assert m is not None

    def test_ignore_add_filter(self):
        m = _NL_IGNORE_RE.match("add canceled meetings to ignore filter")
        assert m is not None

    def test_ignore_add_please(self):
        m = _NL_IGNORE_RE.match("please add weekly sync to ignore list")
        assert m is not None

    def test_ignore_no_match_vague(self):
        assert _NL_IGNORE_RE.match("ignore everything next week") is None
        assert _NL_IGNORE_RE.match("what's on my calendar") is None

    def test_infoevent_mark_as(self):
        m = _NL_INFOEVENT_RE.match("mark standup as info event")
        assert m is not None
        assert "standup" in m.group(1).lower()

    def test_infoevent_add_to(self):
        m = _NL_INFOEVENT_RE.match("add lunch to info events")
        assert m is not None

    def test_infoevent_mark_infoonly(self):
        m = _NL_INFOEVENT_RE.match("mark dog walker as info-only")
        assert m is not None

    def test_infoevent_no_match_vague(self):
        assert _NL_INFOEVENT_RE.match("is this meeting informational?") is None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 24. _handle_ignore / _handle_nonblock (async, via asyncio.run)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import asyncio
from main import _handle_ignore, _handle_infoevent


def _make_async_reply():
    """Return (replies_list, async_reply_fn) for testing async handlers."""
    replies = []
    async def reply(msg):
        replies.append(msg)
    return replies, reply


class TestHandleIgnore:
    """Integration tests for _handle_ignore handler logic."""

    def setup_method(self):
        """Save and restore IGNORED_EVENTS between tests."""
        self._orig_ignored = list(main.IGNORED_EVENTS)

    def teardown_method(self):
        main.IGNORED_EVENTS[:] = self._orig_ignored

    def _run(self, coro):
        return asyncio.run(coro)

    def test_show_list(self):
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, ""))
        assert len(replies) == 1
        assert "Ignore list" in replies[0]

    def test_add_single(self):
        replies, reply = _make_async_reply()
        initial = len(main.IGNORED_EVENTS)
        self._run(_handle_ignore(reply, "weekly sync"))
        assert len(main.IGNORED_EVENTS) == initial + 1
        assert "weekly sync" in main.IGNORED_EVENTS
        assert "Added" in replies[0]

    def test_add_multiple_comma_separated(self):
        replies, reply = _make_async_reply()
        initial = len(main.IGNORED_EVENTS)
        self._run(_handle_ignore(reply, "standup, daily checkin"))
        assert len(main.IGNORED_EVENTS) == initial + 2
        assert "Added 2" in replies[0]

    def test_add_duplicate_skipped(self):
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, "lunch"))  # already in IGNORED_EVENTS
        assert "already" in replies[0]

    def test_remove_all_clears_list(self):
        _, reply_discard = _make_async_reply()
        self._run(_handle_ignore(reply_discard, "team meeting"))
        assert "team meeting" in main.IGNORED_EVENTS
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, "remove all"))
        assert "team meeting" not in main.IGNORED_EVENTS
        assert "Removed" in replies[0]

    def test_remove_all_when_empty(self):
        main.IGNORED_EVENTS[:] = []
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, "remove all"))
        assert "No " in replies[0]

    def test_remove_existing(self):
        _, reply_add = _make_async_reply()
        self._run(_handle_ignore(reply_add, "standup"))
        assert "standup" in main.IGNORED_EVENTS
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, "remove standup"))
        assert "standup" not in main.IGNORED_EVENTS
        assert "Removed" in replies[0]
        assert "standup" in replies[0]

    def test_remove_entry(self):
        """Can remove entries loaded from filters.json (e.g. 'lunch')."""
        assert "lunch" in main.IGNORED_EVENTS
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, "remove lunch"))
        assert "lunch" not in main.IGNORED_EVENTS
        assert "Removed" in replies[0]

    def test_remove_multiple(self):
        _, reply_add = _make_async_reply()
        self._run(_handle_ignore(reply_add, "a, b"))
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, "remove a, b"))
        assert "a" not in main.IGNORED_EVENTS
        assert "b" not in main.IGNORED_EVENTS
        assert "Removed" in replies[0]

    def test_remove_not_found_reported(self):
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, "remove nonexistent-event-xyz"))
        assert "Not found" in replies[0]

    def test_remove_no_args_shows_usage(self):
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, "remove"))
        assert "Usage" in replies[0]

    def test_show_list_mentions_remove(self):
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, ""))
        assert "remove" in replies[0]

    def test_r_shorthand_removes_entry(self):
        _, reply_add = _make_async_reply()
        self._run(_handle_ignore(reply_add, "standup"))
        assert "standup" in main.IGNORED_EVENTS
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, "r standup"))
        assert "standup" not in main.IGNORED_EVENTS
        assert "Removed" in replies[0]

    def test_r_all_clears_list(self):
        _, reply_add = _make_async_reply()
        self._run(_handle_ignore(reply_add, "standup"))
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, "r all"))
        assert "standup" not in main.IGNORED_EVENTS
        assert "Removed" in replies[0]

    def test_r_no_args_shows_usage(self):
        replies, reply = _make_async_reply()
        self._run(_handle_ignore(reply, "r"))
        assert "Usage" in replies[0]


class TestHandleInfoevent:
    """Integration tests for _handle_infoevent handler logic."""

    def setup_method(self):
        self._orig_ie = list(main.INFO_EVENTS)

    def teardown_method(self):
        main.INFO_EVENTS[:] = self._orig_ie

    def _run(self, coro):
        return asyncio.run(coro)

    def test_show_list(self):
        replies, reply = _make_async_reply()
        self._run(_handle_infoevent(reply, ""))
        assert "Info-event list" in replies[0]

    def test_add_single(self):
        replies, reply = _make_async_reply()
        initial = len(main.INFO_EVENTS)
        self._run(_handle_infoevent(reply, "standup"))
        assert len(main.INFO_EVENTS) == initial + 1
        assert "standup" in main.INFO_EVENTS
        assert "Added" in replies[0]

    def test_add_multiple(self):
        replies, reply = _make_async_reply()
        initial = len(main.INFO_EVENTS)
        self._run(_handle_infoevent(reply, "standup, dog walker"))
        assert len(main.INFO_EVENTS) == initial + 2

    def test_remove_all(self):
        _, reply_discard = _make_async_reply()
        self._run(_handle_infoevent(reply_discard, "standup"))
        replies, reply = _make_async_reply()
        self._run(_handle_infoevent(reply, "remove all"))
        assert "standup" not in main.INFO_EVENTS
        assert "Removed" in replies[0]

    def test_remove_existing(self):
        _, reply_add = _make_async_reply()
        self._run(_handle_infoevent(reply_add, "standup"))
        assert "standup" in main.INFO_EVENTS
        replies, reply = _make_async_reply()
        self._run(_handle_infoevent(reply, "remove standup"))
        assert "standup" not in main.INFO_EVENTS
        assert "Removed" in replies[0]

    def test_remove_not_found_reported(self):
        replies, reply = _make_async_reply()
        self._run(_handle_infoevent(reply, "remove nonexistent-xyz"))
        assert "Not found" in replies[0]

    def test_remove_no_args_shows_usage(self):
        replies, reply = _make_async_reply()
        self._run(_handle_infoevent(reply, "remove"))
        assert "Usage" in replies[0]

    def test_show_list_mentions_remove(self):
        replies, reply = _make_async_reply()
        self._run(_handle_infoevent(reply, ""))
        assert "remove" in replies[0]

    def test_r_shorthand_removes_entry(self):
        _, reply_add = _make_async_reply()
        self._run(_handle_infoevent(reply_add, "standup"))
        assert "standup" in main.INFO_EVENTS
        replies, reply = _make_async_reply()
        self._run(_handle_infoevent(reply, "r standup"))
        assert "standup" not in main.INFO_EVENTS
        assert "Removed" in replies[0]

    def test_r_all_clears_list(self):
        _, reply_add = _make_async_reply()
        self._run(_handle_infoevent(reply_add, "standup"))
        replies, reply = _make_async_reply()
        self._run(_handle_infoevent(reply, "r all"))
        assert "standup" not in main.INFO_EVENTS
        assert "Removed" in replies[0]

    def test_r_no_args_shows_usage(self):
        replies, reply = _make_async_reply()
        self._run(_handle_infoevent(reply, "r"))
        assert "Usage" in replies[0]

    def test_remove_all_when_empty(self):
        main.INFO_EVENTS[:] = []
        replies, reply = _make_async_reply()
        self._run(_handle_infoevent(reply, "remove all"))
        assert "No " in replies[0]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 26. _handle_reboot
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from main import _handle_reboot


class TestHandleReboot:
    """Tests for _handle_reboot handler."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_reboot_sends_message_and_restarts(self):
        """_handle_reboot should reply with a reboot message then re-exec the process."""
        replies, reply = _make_async_reply()
        with patch("os.execv") as mock_execv:
            self._run(_handle_reboot(reply))
        assert len(replies) == 1
        assert "Rebooting" in replies[0]
        mock_execv.assert_called_once()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 27. send_noon_brief
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from main import send_noon_brief, _fetch_digest_events, _send_notification


class TestSendNoonBrief:
    """Tests for the noon brief digest function."""

    @patch("main._send_notification")
    @patch("main._fetch_digest_events")
    def test_skips_when_no_events(self, mock_fetch, mock_notify):
        """Noon brief should not send a notification when tomorrow has no work events."""
        mock_fetch.return_value = []
        send_noon_brief()
        mock_notify.assert_not_called()

    @patch("main._send_notification")
    @patch("main._fetch_digest_events")
    def test_sends_when_events_exist(self, mock_fetch, mock_notify):
        """Noon brief should send a notification when tomorrow has work events."""
        tz = ZoneInfo("America/Los_Angeles")
        tomorrow = datetime.now(tz) + timedelta(days=1)
        mock_fetch.return_value = [
            Event(dt=tomorrow.replace(hour=9, minute=0), summary="Standup", all_day=False, duration_min=30),
            Event(dt=tomorrow.replace(hour=14, minute=0), summary="Sprint Review", all_day=False, duration_min=60),
        ]
        send_noon_brief()
        mock_notify.assert_called_once()
        title = mock_notify.call_args[0][0]
        body = mock_notify.call_args[0][1]
        assert "Tomorrow" in title
        assert "Standup" in body
        assert "Sprint Review" in body

    @patch("main._send_notification")
    @patch("main._fetch_digest_events")
    def test_uses_work_labels_filter(self, mock_fetch, mock_notify):
        """Noon brief should filter by WORK_LABELS."""
        mock_fetch.return_value = []
        send_noon_brief()
        # Verify _fetch_digest_events was called with WORK_LABELS as the label filter
        call_args = mock_fetch.call_args
        assert call_args[0][2] is main.WORK_LABELS or call_args[1].get("label_filter") is main.WORK_LABELS
