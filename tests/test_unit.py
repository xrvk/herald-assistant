"""
Unit tests for Scout Report notification bot.

Tests import directly from main.py — no local reimplementations of production logic.

Run: pytest tests/test_unit.py -v
"""

import os
import time
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from zoneinfo import ZoneInfo

import pytest
import requests

# ── Patch environment before importing main ──
# Provide minimal env vars so main.py doesn't crash on import.
_TEST_ENV = {
    "ICLOUD_URL": "https://fake.cal/feed.ics",
    "ICLOUD_LABEL": "TestCal",
    "OUTLOOK_URL": "",
    "GOOGLE_URL": "",
    "WEEKNIGHT_SCHEDULE": "sun,mon,tue,wed,thu 20:00",
    "WEEKEND_SCHEDULE": "off",
    "NOON_SCHEDULE": "off",
    "TZ": "America/Los_Angeles",
    "APPRISE_URL": "discord://fake/token",
}

# ── Apply env vars before importing main (must happen at module load time) ──
_original_env = {k: os.environ.get(k) for k in _TEST_ENV}
os.environ.update(_TEST_ENV)

import main
from main import (
    _format_duration,
    _parse_schedule,
    _schedule_to_str,
    _load_schedules,
    _save_schedules,
    _fmt_ampm,
    _format_schedules_summary,
    Event,
    get_upcoming_events,
    format_events_for_notification,
    fetch_events,
    _cal_cache,
    _CAL_CACHE_TTL,
    send_noon_brief,
    send_work_update,
    send_weekend_update,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Event Formatting
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
        events = [Event(dt=dt, summary="Standup", duration_min=15, all_day=False)]
        lines = format_events_for_notification(events)
        assert len(lines) == 1
        assert "09:00 AM" in lines[0]
        assert "Standup" in lines[0]
        assert "(15m)" in lines[0]

    def test_all_day_event(self):
        tz = ZoneInfo("America/Los_Angeles")
        dt = datetime(2026, 4, 11, 0, 0, tzinfo=tz)
        events = [Event(dt=dt, summary="Company Holiday", duration_min=None, all_day=True)]
        lines = format_events_for_notification(events)
        assert len(lines) == 1
        assert "All Day" in lines[0]
        assert "Company Holiday" in lines[0]

    def test_empty_list(self):
        assert format_events_for_notification([]) == []

    def test_multiple_events_ordered(self):
        tz = ZoneInfo("America/Los_Angeles")
        events = [
            Event(dt=datetime(2026, 4, 11, 9, 0, tzinfo=tz), summary="Event A", duration_min=30, all_day=False),
            Event(dt=datetime(2026, 4, 11, 14, 0, tzinfo=tz), summary="Event B", duration_min=60, all_day=False),
        ]
        lines = format_events_for_notification(events)
        assert len(lines) == 2
        assert "Event A" in lines[0]
        assert "Event B" in lines[1]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Schedule Parsing
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
#  3. Calendar Fetch — Cache & Error Handling
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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. Fetch Events — Retry Logic
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
#  5. Digest Functions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

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
        call_args = mock_fetch.call_args
        assert call_args[0][2] is main.WORK_LABELS or call_args[1].get("label_filter") is main.WORK_LABELS


class TestSendWorkUpdate:
    """Tests for the weeknight digest function."""

    @patch("main._send_notification")
    @patch("main._fetch_digest_events")
    def test_sends_with_events(self, mock_fetch, mock_notify):
        """Weeknight digest sends notification with event list."""
        tz = ZoneInfo("America/Los_Angeles")
        tomorrow = datetime.now(tz) + timedelta(days=1)
        mock_fetch.return_value = [
            Event(dt=tomorrow.replace(hour=10, minute=0), summary="Team Sync", all_day=False, duration_min=30),
        ]
        send_work_update()
        mock_notify.assert_called_once()
        body = mock_notify.call_args[0][1]
        assert "Team Sync" in body

    @patch("main._send_notification")
    @patch("main._fetch_digest_events")
    def test_sends_no_meetings_message(self, mock_fetch, mock_notify):
        """Weeknight digest sends 'no meetings' when tomorrow is empty."""
        mock_fetch.return_value = []
        send_work_update()
        mock_notify.assert_called_once()
        body = mock_notify.call_args[0][1]
        assert "No meetings" in body

    @patch("main._send_notification")
    @patch("main._fetch_digest_events")
    def test_uses_work_labels_filter(self, mock_fetch, mock_notify):
        """Weeknight digest should filter by WORK_LABELS."""
        mock_fetch.return_value = []
        send_work_update()
        call_args = mock_fetch.call_args
        assert call_args[0][2] is main.WORK_LABELS or call_args[1].get("label_filter") is main.WORK_LABELS


class TestSendWeekendUpdate:
    """Tests for the weekend preview digest function."""

    @patch("main._send_notification")
    @patch("main._fetch_digest_events")
    def test_sends_grouped_by_day(self, mock_fetch, mock_notify):
        """Weekend preview groups events by Fri/Sat/Sun."""
        mock_fetch.return_value = []
        send_weekend_update()
        mock_notify.assert_called_once()
        body = mock_notify.call_args[0][1]
        assert "──" in body
        assert "Nothing planned" in body

    @patch("main._send_notification")
    @patch("main._fetch_digest_events")
    def test_title_contains_weekend(self, mock_fetch, mock_notify):
        """Weekend preview title mentions weekend."""
        mock_fetch.return_value = []
        send_weekend_update()
        title = mock_notify.call_args[0][0]
        assert "Weekend" in title


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  6. Schedule Persistence
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TestScheduleToStr:
    """Test _schedule_to_str helper."""

    def test_none_returns_off(self):
        assert _schedule_to_str(None) == "off"

    def test_tuple_returns_formatted(self):
        assert _schedule_to_str(("mon,tue", 14, 30)) == "mon,tue 14:30"

    def test_roundtrip(self):
        """schedule_to_str output can be parsed back by _parse_schedule."""
        original = ("sun,mon,tue,wed,thu", 18, 0)
        s = _schedule_to_str(original)
        result = _parse_schedule(s, "mon", "08:00")
        assert result == original


class TestFmtAmpm:
    """Test _fmt_ampm helper."""

    def test_morning(self):
        assert _fmt_ampm(9, 0) == "9:00 AM"

    def test_afternoon(self):
        assert _fmt_ampm(14, 30) == "2:30 PM"

    def test_noon(self):
        assert _fmt_ampm(12, 0) == "12:00 PM"

    def test_midnight(self):
        assert _fmt_ampm(0, 0) == "12:00 AM"


class TestSchedulePersistence:
    """Test _load_schedules / _save_schedules."""

    def test_save_and_load(self, tmp_path):
        """Save schedules to file and load them back."""
        f = tmp_path / "schedules.json"
        with patch("main._SCHEDULES_FILE", str(f)):
            _save_schedules({"weeknight": "mon,tue 19:00", "weekend": "off"})
            result = _load_schedules()
        assert result == {"weeknight": "mon,tue 19:00", "weekend": "off"}

    def test_load_missing_file(self, tmp_path):
        """Loading from nonexistent file returns None."""
        f = tmp_path / "nonexistent.json"
        with patch("main._SCHEDULES_FILE", str(f)):
            assert _load_schedules() is None

    def test_load_corrupt_file(self, tmp_path):
        """Loading from corrupt JSON returns None."""
        f = tmp_path / "bad.json"
        f.write_text("not json{{{")
        with patch("main._SCHEDULES_FILE", str(f)):
            assert _load_schedules() is None


class TestFormatSchedulesSummary:
    """Test _format_schedules_summary."""

    def test_with_schedules(self):
        with patch("main._weeknight", ("mon,tue", 18, 0)), \
             patch("main._weekend", ("thu", 15, 0)), \
             patch("main._noon_brief", None):
            lines = _format_schedules_summary()
        assert "Weeknight: mon,tue at 6:00 PM" in lines[0]
        assert "Weekend: thu at 3:00 PM" in lines[1]
        assert "Noon brief: off" in lines[2]

    def test_all_off(self):
        with patch("main._weeknight", None), \
             patch("main._weekend", None), \
             patch("main._noon_brief", None):
            lines = _format_schedules_summary()
        assert all("off" in l for l in lines)

