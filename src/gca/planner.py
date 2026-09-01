"""The planner.

Wiring Graph and ClickUp is the easy half. This is the half that decides whether
the principal trusts the thing: turning "give Atlas four hours this week, nothing
before nine, keep Friday afternoon clear" into a concrete set of proposed
changes that provably respect his stated preferences -- and refusing to propose
anything that does not.

Two rules matter more than the scheduling maths:

  1. The planner never writes. It returns a proposal. Writing is a separate,
     explicitly approved step.
  2. The agent will reschedule a block it owns. It will NOT move a meeting
     organised by an outside party, because moving that sends an email in the
     principal's name to a counterparty. Those are flagged for a human instead.
     An agent that quietly emails your investors is not an agent you can keep.
"""
import uuid
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from psycopg2.extras import Json

from . import preferences
from .db import cursor

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday"]
INTERNAL_ORGANIZERS = {"agent", "self", "assistant"}


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _at(day: datetime, clock: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day.date(), clock, tzinfo=tz)


def _merge(intervals):
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _free_slots(win_start, win_end, busy, buffer_min):
    buf = timedelta(minutes=buffer_min)
    padded = _merge([(s - buf, e + buf) for s, e in busy])
    slots, cur = [], win_start
    for s, e in padded:
        if s > cur:
            slots.append((cur, min(s, win_end)))
        cur = max(cur, e)
        if cur >= win_end:
            break
    if cur < win_end:
        slots.append((cur, win_end))
    return [(s, e) for s, e in slots if e > s]


def week_bounds(week_start: datetime, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(week_start.date(), time(0, 0), tzinfo=tz)
    return start, start + timedelta(days=7)


def propose(c, cal, project: str, hours: float, week_start: datetime,
            intent: str | None = None) -> dict:
    """Build a plan. Reads calendar state; writes nothing but the plan row."""
    prefs = preferences.get(c)
    tz = ZoneInfo(prefs["timezone"])
    ws, we = week_bounds(week_start, tz)
    existing = cal.list_events(ws, we)

    earliest = _t(prefs["no_meetings_before"])
    latest = _t(prefs["no_meetings_after"])
    day_start = max(_t(prefs["workday"]["start"]), earliest)
    day_end = min(_t(prefs["workday"]["end"]), latest)
    buffer_min = prefs["buffer_minutes"]
    min_block = prefs["min_focus_block_minutes"]
    max_block = prefs["max_focus_block_minutes"]
    max_meetings = prefs["max_meetings_per_day"]
    max_blocks_per_day = prefs.get("max_focus_blocks_per_day", 1)

    actions: list[dict] = []
    respected: list[str] = [
        f"No meetings before {prefs['no_meetings_before']}",
        f"No meetings after {prefs['no_meetings_after']}",
        f"{buffer_min}-minute buffer around every existing commitment",
        f"Focus blocks between {min_block} and {max_block} minutes",
        f"At most {max_meetings} meetings per day",
    ] + [f"Protected: {b['label']}" for b in prefs["protected_blocks"]]

    # --- pass 1: existing commitments that violate the preferences ---------
    protected_by_day: dict[int, list[tuple[datetime, datetime, str]]] = {}
    for b in prefs["protected_blocks"]:
        idx = WEEKDAYS.index(b["day"].lower())
        day = ws + timedelta(days=idx)
        protected_by_day.setdefault(idx, []).append(
            (_at(day, _t(b["start"]), tz), _at(day, _t(b["end"]), tz), b["label"]))

    for ev in existing:
        idx = ev.starts_at.astimezone(tz).weekday()
        local_start = ev.starts_at.astimezone(tz)
        internal = (ev.organizer or "").lower() in INTERNAL_ORGANIZERS

        if local_start.timetz().replace(tzinfo=None) < earliest:
            reason = (f"starts {local_start:%H:%M}, before your "
                      f"{prefs['no_meetings_before']} rule")
            if internal:
                new_start = _at(local_start, day_start, tz) + timedelta(minutes=buffer_min)
                dur = ev.ends_at - ev.starts_at
                actions.append({
                    "kind": "move_event", "event_id": ev.id, "subject": ev.subject,
                    "from": {"starts_at": ev.starts_at.isoformat(),
                             "ends_at": ev.ends_at.isoformat()},
                    "to": {"starts_at": new_start.isoformat(),
                           "ends_at": (new_start + dur).isoformat()},
                    "reason": reason + " — yours to move, so rescheduling it",
                })
            else:
                actions.append({
                    "kind": "flag_conflict", "event_id": ev.id, "subject": ev.subject,
                    "when": ev.starts_at.isoformat(),
                    "reason": reason,
                    "requires": "your decision — organised by an outside party, so "
                                "moving it would email them in your name",
                })
            continue

        for p_start, p_end, label in protected_by_day.get(idx, []):
            if ev.starts_at < p_end and ev.ends_at > p_start:
                actions.append({
                    "kind": "flag_conflict", "event_id": ev.id, "subject": ev.subject,
                    "when": ev.starts_at.isoformat(),
                    "reason": f"lands inside '{label}'",
                    "requires": ("your decision — organised by an outside party"
                                 if not internal else "your decision"),
                })

    # --- pass 2: allocate the requested hours ------------------------------
    moved = {a["event_id"]: a for a in actions if a["kind"] == "move_event"}
    remaining = timedelta(hours=hours)
    placed: list[dict] = []

    for offset in range(5):                      # Monday..Friday
        if remaining <= timedelta(0):
            break
        day = ws + timedelta(days=offset)
        win_start, win_end = _at(day, day_start, tz), _at(day, day_end, tz)

        busy = []
        meetings_today = 0
        for ev in existing:
            # An event proposed for a move occupies its NEW slot, not its old
            # one -- including for the day-window test. Filtering on the
            # original time would let the planner book straight over it.
            if ev.id in moved:
                m = moved[ev.id]["to"]
                ev_start = datetime.fromisoformat(m["starts_at"])
                ev_end = datetime.fromisoformat(m["ends_at"])
            else:
                ev_start, ev_end = ev.starts_at, ev.ends_at
            if ev_end <= win_start or ev_start >= win_end:
                continue
            meetings_today += 1
            busy.append((ev_start, ev_end))
        for p_start, p_end, _ in protected_by_day.get(offset, []):
            busy.append((p_start, p_end))

        if meetings_today >= max_meetings:
            continue

        blocks_today = 0
        for slot_start, slot_end in _free_slots(win_start, win_end, busy, buffer_min):
            if remaining <= timedelta(0) or blocks_today >= max_blocks_per_day:
                break
            available = slot_end - slot_start
            if available < timedelta(minutes=min_block):
                continue
            length = min(available, remaining, timedelta(minutes=max_block))
            if length < timedelta(minutes=min_block):
                length = timedelta(minutes=min_block)
                if length > available:
                    continue
            placed.append({
                "kind": "create_event",
                "subject": f"{project} — focus block",
                "starts_at": slot_start.isoformat(),
                "ends_at": (slot_start + length).isoformat(),
                "category": project,
                "reason": (f"{length.total_seconds()/3600:.1f}h toward the "
                           f"{hours:g}h you allocated to {project}"),
            })
            remaining -= length
            blocks_today += 1

    actions.extend(placed)

    shortfall = max(remaining.total_seconds() / 3600, 0)
    if placed:
        due = _at(ws + timedelta(days=4), day_end, tz)
        actions.append({
            "kind": "create_task",
            "name": f"{project} — week of {ws:%d %b %Y}",
            "due_at": due.isoformat(),
            "project": project,
            "description": "Scheduled focus time:\n" + "\n".join(
                f"- {datetime.fromisoformat(b['starts_at']):%a %d %b %H:%M}"
                f"-{datetime.fromisoformat(b['ends_at']):%H:%M}" for b in placed),
            "reason": "keeps ClickUp and the calendar describing the same week",
        })

    plan = {
        "plan_id": f"plan_{uuid.uuid4().hex[:10]}",
        "intent": intent or f"Give {project} {hours:g} hours in the week of {ws:%d %b %Y}",
        "project": project,
        "hours_requested": hours,
        "hours_scheduled": round(hours - shortfall, 2),
        "shortfall_hours": round(shortfall, 2),
        "week_start": ws.isoformat(),
        "actions": actions,
        "respected": respected,
        "status": "proposed",
    }

    with cursor(c) as cur:
        cur.execute(
            "INSERT INTO plans (plan_id, intent, payload) VALUES (%s,%s,%s)",
            (plan["plan_id"], plan["intent"], Json(plan)),
        )
    return plan


def load(c, plan_id: str) -> dict | None:
    with cursor(c) as cur:
        cur.execute("SELECT payload, status FROM plans WHERE plan_id = %s", (plan_id,))
        row = cur.fetchone()
        if not row:
            return None
        plan = row["payload"]
        plan["status"] = row["status"]
        return plan


def mark_applied(c, plan_id: str) -> None:
    with cursor(c) as cur:
        cur.execute("UPDATE plans SET status = 'applied' WHERE plan_id = %s", (plan_id,))
