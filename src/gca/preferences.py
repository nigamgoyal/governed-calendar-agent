"""The principal's weekly operating preferences.

This is the part that makes the system his rather than generic: the planner is
not allowed to propose anything that violates this document, and every rule
here is stated in his language, not in cron syntax.
"""
from psycopg2.extras import Json

from .db import cursor

DEFAULT = {
    "timezone": "America/New_York",
    "workday": {"start": "09:00", "end": "17:00"},
    "no_meetings_before": "09:00",
    "no_meetings_after": "17:00",
    "max_meetings_per_day": 4,
    "min_focus_block_minutes": 60,
    "max_focus_block_minutes": 120,
    "buffer_minutes": 15,
    "protected_blocks": [
        {"day": "friday", "start": "12:00", "end": "17:00",
         "label": "Friday afternoon — protected, no meetings"},
    ],
    "project_allocations": {
        "Project Atlas": 4.0,
        "LATAM Portfolio Review": 2.0,
    },
}

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def get(c) -> dict:
    with cursor(c) as cur:
        cur.execute("SELECT doc FROM preferences WHERE id = 1")
        row = cur.fetchone()
        if row:
            return row["doc"]
    return seed(c)


def seed(c, doc: dict | None = None) -> dict:
    doc = doc or DEFAULT
    with cursor(c) as cur:
        cur.execute(
            "INSERT INTO preferences (id, doc) VALUES (1, %s) "
            "ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc, updated_at = now()",
            (Json(doc),),
        )
    return doc


def set_key(c, key: str, value) -> dict:
    """Set one preference by dotted path, e.g. 'workday.start' or
    'project_allocations.Project Atlas'."""
    doc = get(c)
    parts = key.split(".", 1)
    if len(parts) == 1:
        doc[parts[0]] = value
    else:
        head, tail_key = parts
        if head not in doc or not isinstance(doc[head], dict):
            doc[head] = {}
        doc[head][tail_key] = value
    return seed(c, doc)
