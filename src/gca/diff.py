"""Renders a plan as a diff a non-technical principal can approve at a glance.

The dry run is the consent mechanism. If it is not readable in ten seconds it
does not do its job, so this deliberately reads like a note from an assistant
rather than an API payload.
"""
from datetime import datetime

MARK = {"create_event": "+", "move_event": "~", "flag_conflict": "!", "create_task": "+"}


def _when(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%a %d %b  %H:%M")


def render(plan: dict) -> str:
    L: list[str] = []
    L.append(f"PLAN {plan['plan_id']} — {plan['intent']}")
    L.append("Nothing has been written yet. This is a proposal.")
    L.append("")

    for a in plan["actions"]:
        m = MARK.get(a["kind"], " ")
        if a["kind"] == "create_event":
            s, e = datetime.fromisoformat(a["starts_at"]), datetime.fromisoformat(a["ends_at"])
            L.append(f"  {m} CREATE    {s:%a %d %b  %H:%M}-{e:%H:%M}  {a['subject']}")
            L.append(f"              {a['reason']}")
        elif a["kind"] == "move_event":
            f0 = datetime.fromisoformat(a["from"]["starts_at"])
            f1 = datetime.fromisoformat(a["from"]["ends_at"])
            t0 = datetime.fromisoformat(a["to"]["starts_at"])
            t1 = datetime.fromisoformat(a["to"]["ends_at"])
            L.append(f"  {m} MOVE      {f0:%a %d %b}  {a['subject']}")
            L.append(f"              {f0:%H:%M}-{f1:%H:%M}  ->  {t0:%H:%M}-{t1:%H:%M}")
            L.append(f"              {a['reason']}")
        elif a["kind"] == "flag_conflict":
            L.append(f"  {m} CONFLICT  {_when(a['when'])}  {a['subject']}")
            L.append(f"              {a['reason']}")
            L.append(f"              NOT changed — {a['requires']}")
        elif a["kind"] == "create_task":
            L.append(f"  {m} TASK      {a['name']}")
            L.append(f"              due {_when(a['due_at'])} — {a['reason']}")
        L.append("")

    c = sum(1 for a in plan["actions"] if a["kind"] == "create_event")
    mv = sum(1 for a in plan["actions"] if a["kind"] == "move_event")
    fl = sum(1 for a in plan["actions"] if a["kind"] == "flag_conflict")
    tk = sum(1 for a in plan["actions"] if a["kind"] == "create_task")
    L.append(f"Summary: {c} event(s) created, {mv} moved, {fl} flagged for you, "
             f"{tk} task(s) created.")
    L.append(f"         {plan['hours_scheduled']:g}h of {plan['hours_requested']:g}h "
             f"scheduled" + (f" — {plan['shortfall_hours']:g}h could not fit inside "
                             "your rules." if plan["shortfall_hours"] else "."))
    L.append("")
    L.append("Preferences respected:")
    for r in plan["respected"]:
        L.append(f"  - {r}")
    return "\n".join(L)
