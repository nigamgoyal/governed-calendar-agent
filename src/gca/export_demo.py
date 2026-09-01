"""Export one real run to JSON.

The interactive demo page is generated from this file, so every line it shows is
output the system actually produced. Regenerate it and the page updates; there
is no hand-written copy of the behaviour anywhere.
"""
import json
from datetime import datetime, timedelta

from . import audit, config, db, diff, engine, health, planner, preferences, providers, seed
from .providers.base import ProviderAuthError, ProviderUnreachable
from .providers.sandbox import set_fault


def _events(cal, ws):
    s, e = ws, ws + timedelta(days=7)
    return [ev.to_dict() for ev in cal.list_events(s, e)]


def _audit_rows(c, limit=60):
    rows = audit.tail(c, limit)
    for r in rows:
        r["at"] = r["at"].isoformat()
    return list(reversed(rows))


def build(project: str = "Project Atlas", hours: float = 4.0) -> dict:
    db.reset()
    out: dict = {}
    with db.conn() as c:
        prefs = preferences.seed(c)
        ws = seed.seed_week(c)
        cfg = config.load()
        cal, tsk = providers.calendar(cfg, c), providers.tasks(cfg, c)

        out["generated_at"] = datetime.now().astimezone().isoformat()
        out["timezone"] = prefs["timezone"]
        out["week_start"] = ws.isoformat()
        out["preferences"] = prefs
        out["ask"] = f"Give {project} {hours:g} hours this week."

        # act 1 — the week as it stands
        out["calendar_before"] = _events(cal, ws)

        # act 2 — propose (writes nothing)
        plan = planner.propose(c, cal, project, hours, ws)
        out["plan"] = plan
        out["diff_text"] = diff.render(plan)
        out["calendar_during_proposal"] = _events(cal, ws)   # proof: unchanged

        # act 3 — dry run
        dry = engine.apply_plan(c, cfg, cal, tsk, plan["plan_id"], dry_run=True)
        out["dry_run"] = {"message": dry["message"], "dry_run": dry["dry_run"]}
        out["calendar_after_dry_run"] = _events(cal, ws)      # proof: still unchanged

        # act 4 — approved, applied
        applied = engine.apply_plan(c, cfg, cal, tsk, plan["plan_id"], dry_run=False)
        out["apply"] = applied
        out["calendar_after"] = _events(cal, ws)
        out["tasks_after"] = [t.to_dict() for t in tsk.list_tasks()]

        # act 5 — idempotent replay
        replay = engine.apply_plan(c, cfg, cal, tsk, plan["plan_id"], dry_run=False)
        out["replay"] = replay
        out["calendar_after_replay"] = _events(cal, ws)

        out["audit"] = _audit_rows(c)
        out["health_healthy"] = health.check(c, cal, tsk)

        # act 6 — break it on purpose
        set_fault(c, "calendar", "auth_expired")
        out["health_broken"] = health.check(c, cal, tsk)
        try:
            planner.propose(c, cal, project, hours, ws)
            out["planner_refusal"] = None
        except (ProviderAuthError, ProviderUnreachable) as e:
            out["planner_refusal"] = f"{type(e).__name__}: {e}"
        out["alerts"] = [
            {**e, "at": e["at"].isoformat()}
            for e in health.recent(c, 10) if e["level"] == "alert"
        ]

        # act 7 — recovery
        set_fault(c, "calendar", "healthy")
        out["health_recovered"] = health.check(c, cal, tsk)

    return out


def write(path: str = "demo_run.json", **kw) -> str:
    data = build(**kw)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path
