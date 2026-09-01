"""Apply engine.

Everything that changes the outside world goes through here, which is what makes
the guarantees checkable rather than aspirational:

  * dry_run=True is the default at every layer above this one.
  * Every action carries an idempotency key, so re-running a plan after a
    partial failure resumes instead of double-booking.
  * A provider failure stops the run. A half-applied plan that reports success
    is the failure mode that actually hurts someone.
  * flagged conflicts are never executed, only reported.
"""
from datetime import datetime

from . import audit, health, planner
from .providers.base import ProviderAuthError, ProviderUnreachable


def _key(plan_id: str, i: int, kind: str) -> str:
    return f"{plan_id}:{i}:{kind}"


def apply_plan(c, cfg, cal, tasks, plan_id: str, *, dry_run: bool = True,
               actor: str = "claude") -> dict:
    plan = planner.load(c, plan_id)
    if not plan:
        return {"ok": False, "error": f"unknown plan {plan_id}"}

    if dry_run:
        audit.record(
            c, actor=actor, tool="apply_plan", dry_run=True, plan_id=plan_id,
            params={"plan_id": plan_id, "actions": len(plan["actions"])},
            after={"preview": True}, result="ok",
        )
        return {"ok": True, "dry_run": True, "plan": plan,
                "message": "Preview only. Nothing was written."}

    applied, skipped, errors = [], [], []

    for i, a in enumerate(plan["actions"]):
        kind = a["kind"]
        key = _key(plan_id, i, kind)

        if kind == "flag_conflict":
            audit.record(c, actor=actor, tool="apply_plan", plan_id=plan_id,
                         params=a, target_system="calendar", target_id=a.get("event_id"),
                         result="skipped", error=a["requires"], idempotency_key=key)
            skipped.append({"action": a, "why": a["requires"]})
            continue

        if audit.already_applied(c, key):
            skipped.append({"action": a, "why": "already applied — idempotent replay"})
            continue

        try:
            if kind == "create_event":
                ev = cal.create_event(
                    a["subject"], datetime.fromisoformat(a["starts_at"]),
                    datetime.fromisoformat(a["ends_at"]), category=a.get("category"))
                audit.record(c, actor=actor, tool="create_event", plan_id=plan_id,
                             params=a, target_system=cal.name, target_id=ev.id,
                             before=None, after=ev.to_dict(), idempotency_key=key)
                applied.append({"action": kind, "id": ev.id, "subject": ev.subject})

            elif kind == "move_event":
                before = cal.get_event(a["event_id"])
                ev = cal.move_event(a["event_id"],
                                    datetime.fromisoformat(a["to"]["starts_at"]),
                                    datetime.fromisoformat(a["to"]["ends_at"]))
                audit.record(c, actor=actor, tool="move_event", plan_id=plan_id,
                             params=a, target_system=cal.name, target_id=ev.id,
                             before=before.to_dict() if before else None,
                             after=ev.to_dict(), idempotency_key=key)
                applied.append({"action": kind, "id": ev.id, "subject": ev.subject})

            elif kind == "create_task":
                t = tasks.create_task(
                    a["name"], due_at=datetime.fromisoformat(a["due_at"]),
                    project=a.get("project"), description=a.get("description"))
                audit.record(c, actor=actor, tool="create_task", plan_id=plan_id,
                             params=a, target_system=tasks.name, target_id=t.id,
                             before=None, after=t.to_dict(), idempotency_key=key)
                applied.append({"action": kind, "id": t.id, "subject": t.name})

        except (ProviderAuthError, ProviderUnreachable) as e:
            audit.record(c, actor=actor, tool=kind, plan_id=plan_id, params=a,
                         target_system=getattr(cal, "name", "?"),
                         result="error", error=str(e), idempotency_key=key + ":err")
            health.alert(c, source=kind,
                         message=f"apply_plan halted on {kind}: {e}",
                         detail={"plan_id": plan_id, "action_index": i},
                         webhook=cfg.alert_webhook)
            errors.append({"action": a, "error": str(e)})
            return {
                "ok": False, "dry_run": False, "plan_id": plan_id,
                "applied": applied, "skipped": skipped, "errors": errors,
                "message": (f"Stopped after {len(applied)} of {len(plan['actions'])} "
                            f"actions. An alert was raised. Re-running this plan "
                            f"resumes where it stopped — completed actions will not "
                            f"be repeated."),
            }

    planner.mark_applied(c, plan_id)
    audit.record(c, actor=actor, tool="apply_plan", plan_id=plan_id,
                 params={"plan_id": plan_id},
                 after={"applied": len(applied), "skipped": len(skipped)}, result="ok")
    return {"ok": True, "dry_run": False, "plan_id": plan_id, "applied": applied,
            "skipped": skipped, "errors": [],
            "message": f"Applied {len(applied)} action(s); {len(skipped)} left for you."}
