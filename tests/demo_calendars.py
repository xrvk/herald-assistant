"""
Demo calendar generator for tests and !demo mode.

Generates two realistic 7-day ICS calendars relative to any start date:
  - Work: busy developer (standups, 1:1s, sprint ceremonies, reviews)
  - Personal: developer with a family (school runs, soccer, date night)

Includes events that IGNORED_EVENTS should filter out (lunch, canceled, declined).

Usage:
    from tests.demo_calendars import generate_work_ics, generate_personal_ics
    work_ics = generate_work_ics()        # defaults to start of current week
    personal_ics = generate_personal_ics()
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from icalendar import Calendar, Event

_TZ = ZoneInfo("America/Los_Angeles")


def _monday_of_week(ref: datetime | None = None) -> datetime:
    """Return midnight Monday of the week containing `ref` (default: now)."""
    ref = ref or datetime.now(_TZ)
    monday = ref - timedelta(days=ref.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _evt(cal: Calendar, summary: str, start: datetime, duration_min: int = 60, all_day: bool = False):
    """Add a VEVENT to a calendar."""
    e = Event()
    e.add("summary", summary)
    if all_day:
        e.add("dtstart", start.date())
        e.add("dtend", (start + timedelta(days=1)).date())
    else:
        e.add("dtstart", start)
        e.add("dtend", start + timedelta(minutes=duration_min))
    cal.add_component(e)


def generate_work_ics(ref: datetime | None = None) -> Calendar:
    """
    Generate a 7-day work calendar for a busy developer.

    Events include items that should be caught by IGNORED_EVENTS:
      - "Lunch" (daily)
      - "Canceled: Architecture Meeting"
      - "Declined - Team Building"
    """
    cal = Calendar()
    cal.add("prodid", "-//ScoutReport//DemoWork//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Work")

    mon = _monday_of_week(ref)

    # ── Monday ──
    d = mon
    _evt(cal, "Daily Standup", d.replace(hour=9, minute=0), 15)
    _evt(cal, "Sprint Planning", d.replace(hour=10, minute=0), 60)
    _evt(cal, "Lunch", d.replace(hour=12, minute=0), 60)
    _evt(cal, "PR Review: Auth Service", d.replace(hour=14, minute=0), 30)
    _evt(cal, "Onboarding Sync", d.replace(hour=15, minute=30), 30)

    # ── Tuesday ──
    d = mon + timedelta(days=1)
    _evt(cal, "Daily Standup", d.replace(hour=9, minute=0), 15)
    _evt(cal, "Canceled: Architecture Meeting", d.replace(hour=10, minute=0), 60)
    _evt(cal, "Lunch", d.replace(hour=12, minute=0), 60)
    _evt(cal, "1:1 with Manager", d.replace(hour=14, minute=0), 30)
    _evt(cal, "API Design Session", d.replace(hour=15, minute=0), 90)

    # ── Wednesday ──
    d = mon + timedelta(days=2)
    _evt(cal, "Daily Standup", d.replace(hour=9, minute=0), 15)
    _evt(cal, "Design Review: Payments", d.replace(hour=11, minute=0), 60)
    _evt(cal, "Lunch", d.replace(hour=12, minute=0), 60)
    _evt(cal, "Code Review Session", d.replace(hour=15, minute=0), 60)
    _evt(cal, "Declined - Team Building", d.replace(hour=16, minute=30), 90)

    # ── Thursday ──
    d = mon + timedelta(days=3)
    _evt(cal, "Daily Standup", d.replace(hour=9, minute=0), 15)
    _evt(cal, "Sprint Retro", d.replace(hour=10, minute=0), 60)
    _evt(cal, "Lunch", d.replace(hour=12, minute=0), 60)
    _evt(cal, "Department All-Hands", d.replace(hour=13, minute=0), 60)
    _evt(cal, "Incident Review", d.replace(hour=15, minute=0), 45)

    # ── Friday ──
    d = mon + timedelta(days=4)
    _evt(cal, "Daily Standup", d.replace(hour=9, minute=0), 15)
    _evt(cal, "Backlog Grooming", d.replace(hour=10, minute=30), 45)
    _evt(cal, "Lunch", d.replace(hour=12, minute=0), 60)
    _evt(cal, "Sprint Demo", d.replace(hour=15, minute=0), 30)
    _evt(cal, "Happy Hour", d.replace(hour=16, minute=30), 90)

    # ── Saturday ── (on-call)
    d = mon + timedelta(days=5)
    _evt(cal, "On-Call Rotation", d, all_day=True)

    # ── Sunday ──
    d = mon + timedelta(days=6)
    _evt(cal, "Week Planning (personal)", d.replace(hour=20, minute=0), 30)

    return cal


def generate_personal_ics(ref: datetime | None = None) -> Calendar:
    """
    Generate a 7-day personal calendar for a developer with a family.

    Events include items that should be caught by IGNORED_EVENTS:
      - "Pick up kids" (daily)
      - "Drop off kids" (daily)
    """
    cal = Calendar()
    cal.add("prodid", "-//ScoutReport//DemoPersonal//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Personal")

    mon = _monday_of_week(ref)

    # Weekdays: school runs
    for offset in range(5):
        d = mon + timedelta(days=offset)
        _evt(cal, "Drop off kids", d.replace(hour=7, minute=30), 30)
        _evt(cal, "Pick up kids", d.replace(hour=17, minute=0), 30)

    # ── Monday ──
    d = mon
    _evt(cal, "Gym", d.replace(hour=6, minute=0), 60)

    # ── Tuesday ──
    d = mon + timedelta(days=1)
    _evt(cal, "Guitar Lesson (kid)", d.replace(hour=17, minute=30), 45)

    # ── Wednesday ──
    d = mon + timedelta(days=2)
    _evt(cal, "Dentist Appointment", d.replace(hour=16, minute=30), 60)
    _evt(cal, "Gym", d.replace(hour=6, minute=0), 60)

    # ── Thursday ──
    d = mon + timedelta(days=3)
    _evt(cal, "Parent-Teacher Conference", d.replace(hour=16, minute=0), 30)

    # ── Friday ──
    d = mon + timedelta(days=4)
    _evt(cal, "Date Night", d.replace(hour=19, minute=0), 120)
    _evt(cal, "Gym", d.replace(hour=6, minute=0), 60)

    # ── Saturday ──
    d = mon + timedelta(days=5)
    _evt(cal, "Soccer Practice", d.replace(hour=9, minute=0), 60)
    _evt(cal, "Grocery Shopping", d.replace(hour=11, minute=0), 60)
    _evt(cal, "Family Movie Night", d.replace(hour=19, minute=0), 120)

    # ── Sunday ──
    d = mon + timedelta(days=6)
    _evt(cal, "Family Brunch", d.replace(hour=10, minute=0), 90)
    _evt(cal, "Birthday Party (Emma's friend)", d.replace(hour=14, minute=0), 120)
    _evt(cal, "Meal Prep", d.replace(hour=17, minute=0), 60)

    return cal


# ── Summary stats (for debugging / test output) ──

def calendar_stats(cal: Calendar) -> dict:
    """Return a summary of events in a calendar."""
    events = [c for c in cal.walk() if c.name == "VEVENT"]
    return {
        "total_events": len(events),
        "summaries": [str(e.get("summary")) for e in events],
    }
