"""
90-day demo calendar generators for benchmarking and extended demo mode.

Generates three comprehensive, deterministic calendars spanning 30 days of
history and 60 days into the future:

  - Work:     busy developer/manager schedule (standups, 1:1s, sprints, reviews)
  - Personal: active adult (gym, social, hobbies, appointments)
  - Family:   family with two school-age kids (school runs, activities, events)

Includes events that IGNORED_EVENTS should filter out:
  - "Lunch", "Canceled: ...", "Declined - ..." (work)
  - "Drop off kids", "Pick up kids" (family)

All output is deterministic — no randomness. Variety comes from modular
arithmetic on day offsets and week numbers.

Usage:
    from demo.calendars import generate_work_calendar, generate_personal_calendar
    from demo.calendars import generate_family_calendar, calendar_stats

    work = generate_work_calendar()          # anchored to now
    personal = generate_personal_calendar()
    family = generate_family_calendar()
    print(calendar_stats(work))
"""

from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

_TZ = ZoneInfo("America/Los_Angeles")

_HISTORY_DAYS = 30
_FUTURE_DAYS = 60
_TOTAL_DAYS = _HISTORY_DAYS + _FUTURE_DAYS


def _evt(cal, summary, start, duration_min=60, all_day=False):
    """Add a VEVENT to a calendar."""
    e = Event()
    e.add("summary", summary)
    if all_day:
        if isinstance(start, datetime):
            e.add("dtstart", start.date())
            e.add("dtend", (start + timedelta(days=1)).date())
        else:
            e.add("dtstart", start)
            e.add("dtend", start + timedelta(days=1))
    else:
        e.add("dtstart", start)
        e.add("dtend", start + timedelta(minutes=duration_min))
    cal.add_component(e)


def _base_date(ref):
    """Return midnight of (ref - _HISTORY_DAYS) as the calendar start."""
    ref = ref or datetime.now(_TZ)
    if isinstance(ref, date) and not isinstance(ref, datetime):
        ref = datetime.combine(ref, datetime.min.time(), tzinfo=_TZ)
    return ref.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=_HISTORY_DAYS)


# ---------------------------------------------------------------------------
# Work calendar
# ---------------------------------------------------------------------------

def generate_work_calendar(ref=None):
    cal = Calendar()
    cal.add("prodid", "-//ScoutReport//BenchmarkWork//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Work")

    base = _base_date(ref)

    # ── Company holidays keyed by (week_number, day_of_week) ──
    _holidays = {
        (1, 0): "Company Holiday",
        (4, 4): "Company Wellness Day",
        (7, 0): "Memorial Day (Observed)",
        (9, 3): "Thanksgiving (Observed)",
        (9, 4): "Day After Thanksgiving",
        (12, 0): "New Year's Day (Observed)",
    }

    # ── Canceled / Declined events (IGNORED_EVENTS test targets) ──
    _canceled = {
        (1, 2): "Canceled: Architecture Review",
        (3, 1): "Canceled: Design Sync",
        (5, 3): "Canceled: Vendor Demo",
        (8, 1): "Canceled: Cross-Team Sync",
        (10, 2): "Canceled: Budget Review",
        (11, 4): "Canceled: Offsite Planning",
    }
    _declined = {
        (2, 1): "Declined - Team Building",
        (4, 3): "Declined - All-Hands Social",
        (6, 2): "Declined - Vendor Lunch",
        (8, 3): "Declined - Workshop",
        (11, 1): "Declined - Networking Event",
        (12, 4): "Declined - Training Session",
    }

    # ── Rotating event pools (cycled via week_num modular arithmetic) ──
    _design_reviews = [
        "Design Review: Payments",
        "Design Review: User Dashboard",
        "Design Review: API Gateway",
        "Design Review: Notifications",
        "Design Review: Search Service",
    ]
    _architecture = [
        "Architecture Session: Microservices",
        "Architecture Session: Data Pipeline",
        "Architecture Session: Auth Refactor",
        "Architecture Session: Caching Layer",
    ]
    _interviews = [
        "Interview: Senior Backend",
        "Interview: Frontend Developer",
        "Interview: DevOps Engineer",
        "Interview: Engineering Manager",
        "Interview: Staff Engineer",
    ]
    _pr_reviews = [
        "PR Review: Auth Service",
        "PR Review: Payment Gateway",
        "PR Review: User API",
        "PR Review: Search Index",
        "PR Review: CI Pipeline",
        "PR Review: Database Migration",
    ]
    _onboarding = [
        "Onboarding Sync: New Hire",
        "Onboarding Sync: Intern",
        "Onboarding Sync: Contractor",
    ]
    _incidents = [
        "Production Incident: API Latency",
        "Production Incident: Database Failover",
        "Production Incident: CDN Outage",
        "Production Incident: Memory Leak",
    ]

    for day_offset in range(_TOTAL_DAYS):
        d = base + timedelta(days=day_offset)
        dow = d.weekday()  # 0=Mon … 6=Sun
        week_num = day_offset // 7

        # ── Company holiday — all-day, skip everything else ──
        if (week_num, dow) in _holidays:
            _evt(cal, _holidays[(week_num, dow)], d, all_day=True)
            continue

        # ── Weekdays ──
        if dow < 5:
            _evt(cal, "Daily Standup", d.replace(hour=9, minute=0), 15)
            _evt(cal, "Lunch", d.replace(hour=12, minute=0), 60)

            # Canceled / Declined (IGNORED_EVENTS targets)
            if (week_num, dow) in _canceled:
                _evt(cal, _canceled[(week_num, dow)],
                     d.replace(hour=11, minute=0), 60)
            if (week_num, dow) in _declined:
                _evt(cal, _declined[(week_num, dow)],
                     d.replace(hour=16, minute=0), 60)

            # ── Monday ──
            if dow == 0:
                if week_num % 2 == 0:
                    _evt(cal, "Sprint Planning", d.replace(hour=10, minute=0), 60)
                else:
                    _evt(cal, "Backlog Grooming", d.replace(hour=10, minute=0), 45)
                _evt(cal, _pr_reviews[week_num % len(_pr_reviews)],
                     d.replace(hour=13, minute=30), 30)
                if week_num % 3 == 0:
                    _evt(cal, "Roadmap Review", d.replace(hour=14, minute=30), 60)
                if week_num % 4 == 1:
                    _evt(cal, _onboarding[week_num % len(_onboarding)],
                         d.replace(hour=15, minute=30), 30)

            # ── Tuesday ──
            elif dow == 1:
                _evt(cal, "1:1 with Manager", d.replace(hour=14, minute=0), 30)
                if week_num % 3 == 0:
                    _evt(cal, _architecture[week_num % len(_architecture)],
                         d.replace(hour=10, minute=0), 90)
                elif week_num % 3 == 1:
                    _evt(cal, "API Design Session", d.replace(hour=10, minute=0), 60)
                else:
                    _evt(cal, "Focus Time", d.replace(hour=10, minute=0), 120)
                if week_num % 4 == 2:
                    _evt(cal, _interviews[week_num % len(_interviews)],
                         d.replace(hour=15, minute=30), 45)

            # ── Wednesday ──
            elif dow == 2:
                _evt(cal, "Team Sync", d.replace(hour=10, minute=0), 45)
                if week_num % 4 == 0:
                    _evt(cal, "Department All-Hands", d.replace(hour=13, minute=0), 60)
                if week_num % 3 == 1:
                    _evt(cal, _design_reviews[week_num % len(_design_reviews)],
                         d.replace(hour=14, minute=30), 60)
                if week_num % 6 == 0:
                    _evt(cal, "Quarterly Planning", d.replace(hour=15, minute=30), 120)
                if week_num % 5 == 3:
                    _evt(cal, "Cross-Team Sync", d.replace(hour=11, minute=0), 45)

            # ── Thursday ──
            elif dow == 3:
                _evt(cal, "Code Review Session", d.replace(hour=15, minute=0), 60)
                if week_num % 3 == 2:
                    _evt(cal, _interviews[(week_num + 1) % len(_interviews)],
                         d.replace(hour=10, minute=0), 45)
                if week_num % 2 == 0:
                    _evt(cal, "Tech Talk", d.replace(hour=16, minute=30), 45)
                if week_num % 4 == 3:
                    _evt(cal, "Incident Review", d.replace(hour=14, minute=0), 45)
                if week_num % 5 == 1:
                    _evt(cal, "Platform Team Sync", d.replace(hour=11, minute=0), 30)

            # ── Friday ──
            elif dow == 4:
                if week_num % 2 == 0:
                    _evt(cal, "Sprint Retro", d.replace(hour=10, minute=0), 60)
                    _evt(cal, "Sprint Demo", d.replace(hour=15, minute=0), 30)
                else:
                    _evt(cal, "Knowledge Sharing", d.replace(hour=10, minute=30), 45)
                if week_num % 3 == 0:
                    _evt(cal, "Happy Hour", d.replace(hour=16, minute=30), 90)
                if week_num % 5 == 2:
                    _evt(cal, "Eng All-Hands", d.replace(hour=14, minute=0), 60)
                if week_num % 4 == 1:
                    _evt(cal, _pr_reviews[(week_num + 3) % len(_pr_reviews)],
                         d.replace(hour=13, minute=30), 30)

        # ── Saturday ──
        elif dow == 5:
            if week_num % 4 == 0:
                _evt(cal, "On-Call Rotation", d, all_day=True)
            elif week_num % 4 == 2:
                _evt(cal, _incidents[week_num % len(_incidents)],
                     d.replace(hour=3, minute=15), 120)
            elif week_num % 3 == 1:
                _evt(cal, "Catch Up on Slack", d.replace(hour=10, minute=0), 30)
            else:
                _evt(cal, "Open Source Contributions", d.replace(hour=10, minute=0), 60)

        # ── Sunday ──
        elif dow == 6:
            if week_num % 4 == 0:
                _evt(cal, "On-Call Rotation", d, all_day=True)
            _evt(cal, "Week Planning", d.replace(hour=20, minute=0), 30)

    return cal


# ---------------------------------------------------------------------------
# Personal calendar
# ---------------------------------------------------------------------------

def generate_personal_calendar(ref=None):
    cal = Calendar()
    cal.add("prodid", "-//ScoutReport//BenchmarkPersonal//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Personal")

    base = _base_date(ref)

    _hiking_trails = [
        "Hiking: Eagle Peak Trail",
        "Hiking: Redwood Loop",
        "Hiking: Coastal Bluffs",
        "Hiking: Valley Overlook",
        "Hiking: Summit Lake Trail",
        "Hiking: Canyon Creek",
    ]
    _social_dinners = [
        "Dinner with Alex & Sam",
        "Dinner with College Friends",
        "Dinner at New Italian Place",
        "Dinner with Neighbors",
        "Dinner with Work Friends",
    ]
    _meetups = [
        "Meetup: Python User Group",
        "Meetup: Cloud Native",
        "Meetup: Data Engineering",
        "Meetup: Local Tech Talk",
    ]
    _happy_hours = [
        "Happy Hour with Coworkers",
        "Happy Hour at Rooftop Bar",
        "Happy Hour with College Buddies",
    ]

    # Vacation / long weekend: (week_num, dow)
    _vacation_days = {(6, 0), (6, 1), (6, 2)}
    _long_weekend = {(3, 0)}
    _day_off = {(10, 4)}
    _all_day_off = _vacation_days | _long_weekend | _day_off

    for day_offset in range(_TOTAL_DAYS):
        d = base + timedelta(days=day_offset)
        dow = d.weekday()
        week_num = day_offset // 7

        # ── All-day time-off events (co-exist with regular events on the same day) ──
        if (week_num, dow) in _vacation_days:
            _evt(cal, "Vacation", d, all_day=True)
        if (week_num, dow) in _long_weekend:
            _evt(cal, "Long Weekend Trip", d, all_day=True)
        if (week_num, dow) in _day_off:
            _evt(cal, "Day Off", d, all_day=True)

        # ── Weekdays ──
        if dow < 5:
            # Gym Mon/Wed/Fri at 6:00
            if dow in (0, 2, 4):
                _evt(cal, "Gym", d.replace(hour=6, minute=0), 60)

            # ── Monday ──
            if dow == 0:
                if week_num % 3 == 1:
                    _evt(cal, "Coffee with Jamie", d.replace(hour=7, minute=30), 30)
                if week_num % 5 == 2:
                    _evt(cal, "Volunteering: Code Mentorship",
                         d.replace(hour=18, minute=0), 90)
                if week_num % 4 == 3:
                    _evt(cal, _social_dinners[week_num % len(_social_dinners)],
                         d.replace(hour=19, minute=0), 120)

            # ── Tuesday ──
            elif dow == 1:
                _evt(cal, "Guitar Lesson", d.replace(hour=18, minute=0), 45)
                if week_num % 4 == 0:
                    _evt(cal, _meetups[week_num % len(_meetups)],
                         d.replace(hour=19, minute=30), 120)

            # ── Wednesday ──
            elif dow == 2:
                if week_num % 2 == 0:
                    _evt(cal, "Photography Club", d.replace(hour=19, minute=0), 90)
                if week_num % 4 == 3:
                    _evt(cal, _social_dinners[(week_num + 2) % len(_social_dinners)],
                         d.replace(hour=19, minute=0), 120)

            # ── Thursday ──
            elif dow == 3:
                _evt(cal, "Evening Yoga", d.replace(hour=18, minute=30), 60)
                if week_num % 4 == 0:
                    _evt(cal, "Book Club", d.replace(hour=19, minute=30), 120)
                if week_num % 5 == 4:
                    _evt(cal, _meetups[(week_num + 1) % len(_meetups)],
                         d.replace(hour=19, minute=0), 90)

            # ── Friday ──
            elif dow == 4:
                if week_num % 2 == 0:
                    _evt(cal, "Date Night", d.replace(hour=19, minute=0), 120)
                elif week_num % 3 == 1:
                    _evt(cal, _happy_hours[week_num % len(_happy_hours)],
                         d.replace(hour=17, minute=30), 90)
                if week_num % 5 == 3:
                    _evt(cal, _social_dinners[(week_num + 1) % len(_social_dinners)],
                         d.replace(hour=19, minute=0), 120)

            # ── Scattered appointments ──
            if (week_num, dow) == (1, 3):
                _evt(cal, "Dentist Appointment", d.replace(hour=16, minute=0), 60)
            if (week_num, dow) == (3, 1):
                _evt(cal, "Doctor: Annual Physical", d.replace(hour=15, minute=0), 60)
            if (week_num, dow) == (5, 2):
                _evt(cal, "Haircut", d.replace(hour=17, minute=0), 30)
            if (week_num, dow) == (7, 0):
                _evt(cal, "Car Maintenance: Oil Change",
                     d.replace(hour=8, minute=0), 60)
            if (week_num, dow) == (9, 3):
                _evt(cal, "Eye Doctor", d.replace(hour=14, minute=0), 60)
            if (week_num, dow) == (11, 1):
                _evt(cal, "Dentist: Cleaning", d.replace(hour=16, minute=0), 60)
            if (week_num, dow) == (8, 4):
                _evt(cal, "Chiropractor", d.replace(hour=16, minute=30), 45)
            if (week_num, dow) == (2, 2):
                _evt(cal, "Car Wash", d.replace(hour=8, minute=0), 30)
            if (week_num, dow) == (4, 0):
                _evt(cal, "DMV Appointment", d.replace(hour=10, minute=0), 120)
            if (week_num, dow) == (10, 1):
                _evt(cal, "Financial Advisor", d.replace(hour=17, minute=0), 60)

        # ── Saturday ──
        elif dow == 5:
            if week_num % 2 == 0:
                _evt(cal, _hiking_trails[week_num % len(_hiking_trails)],
                     d.replace(hour=7, minute=0), 180)
            else:
                _evt(cal, "Farmers Market", d.replace(hour=9, minute=0), 90)
            if week_num % 3 == 0:
                _evt(cal, "Photography Class", d.replace(hour=14, minute=0), 120)
            if week_num % 4 == 1:
                _evt(cal, "Brunch with Friends", d.replace(hour=11, minute=0), 90)
            if week_num % 3 == 2:
                _evt(cal, _social_dinners[week_num % len(_social_dinners)],
                     d.replace(hour=19, minute=0), 120)
            if week_num % 5 == 4:
                _evt(cal, "House Cleaning", d.replace(hour=13, minute=0), 120)

        # ── Sunday ──
        elif dow == 6:
            _evt(cal, "Meal Prep", d.replace(hour=17, minute=0), 60)
            if week_num % 2 == 0:
                _evt(cal, "Brunch with Friends", d.replace(hour=10, minute=0), 90)
            else:
                _evt(cal, "Morning Walk", d.replace(hour=8, minute=0), 60)
            if week_num % 3 == 0:
                _evt(cal, "Call Parents", d.replace(hour=15, minute=0), 30)
            if week_num % 4 == 2:
                _evt(cal, "Batch Cooking", d.replace(hour=14, minute=0), 120)
            if week_num % 5 == 1:
                _evt(cal, "Journaling + Planning", d.replace(hour=20, minute=0), 30)

    return cal


# ---------------------------------------------------------------------------
# Family calendar
# ---------------------------------------------------------------------------

def generate_family_calendar(ref=None):
    cal = Calendar()
    cal.add("prodid", "-//ScoutReport//BenchmarkFamily//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Family")

    base = _base_date(ref)

    _birthday_parties = [
        "Birthday Party: Emma's Friend",
        "Birthday Party: Liam at Bounce House",
        "Birthday Party: Sophia's Pool Party",
        "Birthday Party: Noah at Laser Tag",
        "Birthday Party: Olivia's Art Party",
    ]
    _playdates = [
        "Playdate with the Johnsons",
        "Playdate at the Park",
        "Playdate: Board Games",
        "Playdate with School Friends",
    ]
    _school_events = [
        ("Science Fair", 120),
        ("School Play: Spring Musical", 120),
        ("School Carnival", 180),
        ("Field Day", 240),
        ("School Picture Day", 30),
    ]

    # No-school days: spring break (week 4 Mon-Fri), memorial day, thanksgiving
    _no_school = set()
    for wd in range(5):
        _no_school.add((4, wd))
    _no_school.add((7, 0))
    _no_school.add((9, 3))
    _no_school.add((9, 4))

    for day_offset in range(_TOTAL_DAYS):
        d = base + timedelta(days=day_offset)
        dow = d.weekday()
        week_num = day_offset // 7

        # ── Spring break week — special handling ──
        if week_num == 4 and dow < 5:
            _evt(cal, "Spring Break", d, all_day=True)
            if dow == 0:
                _evt(cal, "Museum Visit", d.replace(hour=10, minute=0), 180)
            elif dow in (1, 3):
                _evt(cal, "Spring Break Camp", d.replace(hour=9, minute=0), 180)
            elif dow == 2:
                _evt(cal, "Family Day Trip", d.replace(hour=8, minute=0), 480)
            elif dow == 4:
                _evt(cal, "Playdate at the Park", d.replace(hour=10, minute=0), 120)
            continue

        # ── Memorial Day ──
        if (week_num, dow) == (7, 0):
            _evt(cal, "Memorial Day BBQ", d, all_day=True)
            _evt(cal, "Memorial Day Parade", d.replace(hour=10, minute=0), 120)
            continue

        # ── Thanksgiving ──
        if (week_num, dow) == (9, 3):
            _evt(cal, "Thanksgiving", d, all_day=True)
            _evt(cal, "Thanksgiving Dinner", d.replace(hour=14, minute=0), 300)
            continue
        if (week_num, dow) == (9, 4):
            _evt(cal, "Day After Thanksgiving", d, all_day=True)
            _evt(cal, "Family Movie Marathon", d.replace(hour=10, minute=0), 360)
            continue

        # ── Weekdays ──
        if dow < 5:
            # School drop-off and pick-up (IGNORED_EVENTS targets)
            _evt(cal, "Drop off kids", d.replace(hour=7, minute=30), 15)
            _evt(cal, "Pick up kids", d.replace(hour=15, minute=0), 15)

            # ── Monday ──
            if dow == 0:
                if week_num % 3 == 0:
                    _evt(cal, "School Assembly", d.replace(hour=9, minute=0), 60)
                if week_num % 4 == 2:
                    _evt(cal, _playdates[week_num % len(_playdates)],
                         d.replace(hour=15, minute=30), 120)
                if week_num % 5 == 4:
                    _evt(cal, "Homework Help Night", d.replace(hour=18, minute=0), 60)

            # ── Tuesday ──
            elif dow == 1:
                _evt(cal, "Piano Lesson: Emma", d.replace(hour=16, minute=0), 45)
                if week_num % 4 == 1:
                    _evt(cal, _playdates[(week_num + 1) % len(_playdates)],
                         d.replace(hour=17, minute=0), 90)

            # ── Wednesday ──
            elif dow == 2:
                _evt(cal, "Swimming: Liam", d.replace(hour=16, minute=0), 60)
                if week_num % 4 == 2:
                    _evt(cal, _playdates[(week_num + 2) % len(_playdates)],
                         d.replace(hour=17, minute=30), 90)

            # ── Thursday ──
            elif dow == 3:
                if week_num % 2 == 0:
                    _evt(cal, "Art Class: Emma", d.replace(hour=16, minute=0), 60)
                if week_num % 3 == 2:
                    _evt(cal, "Library Story Time", d.replace(hour=17, minute=0), 45)

            # ── Friday ──
            elif dow == 4:
                if week_num % 2 == 1:
                    _evt(cal, "Family Game Night", d.replace(hour=19, minute=0), 120)
                if week_num % 5 == 0:
                    _evt(cal, "Pizza Night", d.replace(hour=18, minute=0), 60)
                if week_num % 4 == 3:
                    _evt(cal, "Movie Night (kids pick)",
                         d.replace(hour=19, minute=0), 120)

            # ── School events (scattered across the 90 days) ──
            if (week_num, dow) == (2, 3):
                _evt(cal, "Parent-Teacher Conference",
                     d.replace(hour=16, minute=30), 30)
            if (week_num, dow) == (5, 4):
                name, dur = _school_events[0]
                _evt(cal, name, d.replace(hour=13, minute=0), dur)
            if (week_num, dow) == (8, 4):
                name, dur = _school_events[1]
                _evt(cal, name, d.replace(hour=18, minute=0), dur)
            if (week_num, dow) == (11, 3):
                _evt(cal, "Parent-Teacher Conference",
                     d.replace(hour=16, minute=30), 30)
            if (week_num, dow) == (6, 2):
                name, dur = _school_events[2]
                _evt(cal, name, d.replace(hour=15, minute=30), dur)
            if (week_num, dow) == (10, 4):
                name, dur = _school_events[3]
                _evt(cal, name, d.replace(hour=9, minute=0), dur)
            if (week_num, dow) == (3, 1):
                name, dur = _school_events[4]
                _evt(cal, name, d.replace(hour=8, minute=0), dur)

            # ── Medical / vet appointments ──
            if (week_num, dow) == (3, 2):
                _evt(cal, "Pediatrician: Annual Checkup",
                     d.replace(hour=10, minute=0), 60)
            if (week_num, dow) == (7, 1):
                _evt(cal, "Vet Appointment: Dog",
                     d.replace(hour=9, minute=0), 45)
            if (week_num, dow) == (10, 3):
                _evt(cal, "Pediatrician: Flu Shots",
                     d.replace(hour=15, minute=30), 30)
            if (week_num, dow) == (5, 1):
                _evt(cal, "Dentist: Kids Cleaning",
                     d.replace(hour=10, minute=0), 60)
            if (week_num, dow) == (12, 1):
                _evt(cal, "Vet: Annual Vaccinations",
                     d.replace(hour=11, minute=0), 45)
            if (week_num, dow) == (8, 2):
                _evt(cal, "Pediatrician: Liam Checkup",
                     d.replace(hour=10, minute=0), 45)

        # ── Saturday ──
        elif dow == 5:
            _evt(cal, "Soccer Practice: Liam", d.replace(hour=9, minute=0), 60)
            _evt(cal, "Family Movie Night", d.replace(hour=19, minute=0), 120)
            if week_num % 3 == 0:
                _evt(cal, _birthday_parties[week_num % len(_birthday_parties)],
                     d.replace(hour=14, minute=0), 120)
            if week_num % 2 == 1:
                _evt(cal, "Grocery Shopping", d.replace(hour=11, minute=0), 60)
            if week_num % 4 == 1:
                _evt(cal, "Home Project", d.replace(hour=13, minute=0), 180)
            if week_num % 5 == 4:
                _evt(cal, "Yard Work", d.replace(hour=8, minute=0), 120)

        # ── Sunday ──
        elif dow == 6:
            _evt(cal, "Family Dinner", d.replace(hour=18, minute=0), 90)
            if week_num % 2 == 0:
                _evt(cal, "Church", d.replace(hour=9, minute=0), 90)
            else:
                _evt(cal, "Family Brunch", d.replace(hour=10, minute=0), 90)
            if week_num % 3 == 1:
                _evt(cal, "Park / Playground", d.replace(hour=14, minute=0), 120)
            if week_num % 4 == 3:
                _evt(cal, "Visit Grandparents", d.replace(hour=13, minute=0), 180)
            if week_num % 5 == 2:
                _evt(cal, "Family Bike Ride", d.replace(hour=15, minute=0), 90)

    return cal


# ---------------------------------------------------------------------------
# Stats helper
# ---------------------------------------------------------------------------

def calendar_stats(cal):
    """Return a summary of events in a calendar."""
    events = [c for c in cal.walk() if c.name == "VEVENT"]
    return {
        "total_events": len(events),
        "summaries": [str(e.get("summary")) for e in events],
    }
