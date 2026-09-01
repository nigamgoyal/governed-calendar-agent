"""Demo fixture: one realistic week in the principal's calendar.

Deliberately contains two preference violations of different kinds, because the
interesting behaviour is what the agent does with a conflict it is not allowed
to resolve on its own.
"""
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from . import preferences
from .db import cursor
from .providers.sandbox import SandboxCalendar

FIXTURE = [
    # day offset, start, end, subject, organizer
    (0, "08:00", "08:30", "Internal prep — Atlas SPV", "agent"),
    (0, "11:00", "12:00", "Engineering — module reliability review", "external"),
    (1, "15:00", "16:00", "Risk committee", "external"),
    (2, "09:30", "10:30", "LATAM counterparty — Bogotá SPV", "external"),
    (3, "13:00", "14:00", "Board prep", "external"),
    (4, "14:00", "15:00", "Investor update — LATAM fund", "external"),
]


def monday_of(d: datetime) -> datetime:
    return d - timedelta(days=d.weekday())


def seed_week(c, week_start: datetime | None = None) -> datetime:
    prefs = preferences.get(c)
    tz = ZoneInfo(prefs["timezone"])
    ws = monday_of(week_start or datetime.now(tz))
    ws = datetime.combine(ws.date(), time(0, 0), tzinfo=tz)

    with cursor(c) as cur:
        cur.execute("DELETE FROM sandbox_calendar_events")

    cal = SandboxCalendar(c)
    for offset, s, e, subject, organizer in FIXTURE:
        day = ws + timedelta(days=offset)
        sh, sm = map(int, s.split(":"))
        eh, em = map(int, e.split(":"))
        cal.seed_event(subject,
                       datetime.combine(day.date(), time(sh, sm), tzinfo=tz),
                       datetime.combine(day.date(), time(eh, em), tzinfo=tz),
                       organizer=organizer)
    return ws
