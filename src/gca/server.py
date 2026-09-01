"""MCP server — the surface Claude actually talks to.

Tool design is the governance boundary. Note what is *not* here: there is no
generic `run_sql`, no `graph_request`, no `delete_everything`. Claude can only
express intents this server already knows how to make safe, and the only tool
that changes anything requires a plan id that a human has seen rendered as a
diff first.

  read     get_week, get_preferences, get_audit_trail, health_check
  propose  propose_plan            (writes nothing)
  write    apply_plan              (dry_run defaults to True)
  config   set_preference
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer

from . import audit, config, db, diff, engine, health, planner, preferences, providers
from .seed import monday_of

mcp = MCPServer(
    "governed-calendar",
    instructions=(
        "Scoped, audited access to the principal's calendar and project tasks. "
        "ALWAYS propose_plan first, show the user the dry run, and only call "
        "apply_plan with dry_run=false after they explicitly approve. Never "
        "describe a change as done unless apply_plan returned ok with dry_run=false."
    ),
)


def _week_start(week_start: str | None, tz: ZoneInfo) -> datetime:
    if week_start:
        return monday_of(datetime.fromisoformat(week_start).replace(tzinfo=tz))
    return monday_of(datetime.now(tz))


@mcp.tool(description="Read the principal's calendar and open tasks for a week. Read-only.")
def get_week(week_start: str | None = None) -> dict:
    cfg = config.load()
    with db.conn() as c:
        cal, tsk = providers.calendar(cfg, c), providers.tasks(cfg, c)
        tz = ZoneInfo(preferences.get(c)["timezone"])
        ws = _week_start(week_start, tz)
        s, e = planner.week_bounds(ws, tz)
        events = [ev.to_dict() for ev in cal.list_events(s, e)]
        audit.record(c, actor="claude", tool="get_week",
                     params={"week_start": ws.isoformat()},
                     after={"events": len(events)}, result="ok")
        return {"week_start": ws.isoformat(), "events": events,
                "tasks": [t.to_dict() for t in tsk.list_tasks()]}


@mcp.tool(description="Return the principal's stored weekly operating preferences.")
def get_preferences() -> dict:
    with db.conn() as c:
        return preferences.get(c)


@mcp.tool(description=(
    "Set one preference by dotted path, e.g. key='no_meetings_before' value='09:30', "
    "or key='project_allocations.Project Atlas' value=6. Audited."))
def set_preference(key: str, value: str) -> dict:
    parsed: object = value
    try:
        parsed = float(value) if "." in value else int(value)
    except ValueError:
        pass
    with db.conn() as c:
        before = preferences.get(c)
        after = preferences.set_key(c, key, parsed)
        audit.record(c, actor="claude", tool="set_preference",
                     params={"key": key, "value": parsed},
                     before=before, after=after, result="ok")
        return after


@mcp.tool(description=(
    "Propose how to give a project a number of hours in a week, respecting every "
    "stored preference. Writes NOTHING. Returns a plan id and a human-readable "
    "diff to show the user for approval."))
def propose_plan(project: str, hours: float, week_start: str | None = None) -> dict:
    cfg = config.load()
    with db.conn() as c:
        cal = providers.calendar(cfg, c)
        tz = ZoneInfo(preferences.get(c)["timezone"])
        plan = planner.propose(c, cal, project, hours, _week_start(week_start, tz))
        audit.record(c, actor="claude", tool="propose_plan", plan_id=plan["plan_id"],
                     params={"project": project, "hours": hours},
                     after={"actions": len(plan["actions"])}, result="ok", dry_run=True)
        return {"plan_id": plan["plan_id"], "diff": diff.render(plan), "plan": plan}


@mcp.tool(description=(
    "Apply a proposed plan. dry_run=true (the default) returns the diff and changes "
    "nothing. Call with dry_run=false ONLY after the user has approved the diff. "
    "Idempotent: re-running a plan resumes rather than duplicating."))
def apply_plan(plan_id: str, dry_run: bool = True) -> dict:
    cfg = config.load()
    with db.conn() as c:
        cal, tsk = providers.calendar(cfg, c), providers.tasks(cfg, c)
        res = engine.apply_plan(c, cfg, cal, tsk, plan_id, dry_run=dry_run)
        if res.get("dry_run") and res.get("plan"):
            res["diff"] = diff.render(res["plan"])
        return res


@mcp.tool(description="Recent entries from the audit trail: who did what, before and after.")
def get_audit_trail(limit: int = 25, plan_id: str | None = None) -> dict:
    with db.conn() as c:
        rows = audit.tail(c, limit, plan_id)
        for r in rows:
            r["at"] = r["at"].isoformat()
        return {"entries": rows}


@mcp.tool(description=(
    "Check every connected system. Raises and records an alert on any failure. "
    "Run this before reporting that the system is working."))
def health_check() -> dict:
    cfg = config.load()
    with db.conn() as c:
        cal, tsk = providers.calendar(cfg, c), providers.tasks(cfg, c)
        return health.check(c, cal, tsk, webhook=cfg.alert_webhook)


def main() -> None:
    db.init()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
