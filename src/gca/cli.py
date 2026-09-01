"""Demo driver.

Runs the whole trust story from a terminal, so it can be shown without wiring a
Claude client first. The MCP server exposes exactly these same operations.
"""
import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from . import audit, config, db, diff, engine, export_demo, health, planner, preferences, seed
from . import providers
from .providers.sandbox import set_fault

BAR = "─" * 78


def hdr(n, title):
    print(f"\n\033[1m{BAR}\n {n}  {title}\n{BAR}\033[0m")


def _ctx(c):
    cfg = config.load()
    return cfg, providers.calendar(cfg, c), providers.tasks(cfg, c)


def cmd_reset(_a):
    db.reset()
    with db.conn() as c:
        preferences.seed(c)
        ws = seed.seed_week(c)
    print(f"Reset. Seeded the week of {ws:%d %b %Y}.")


def cmd_week(_a):
    with db.conn() as c:
        cfg, cal, _ = _ctx(c)
        tz = ZoneInfo(cfg.timezone)
        ws = seed.monday_of(datetime.now(tz))
        s, e = planner.week_bounds(ws, tz)
        for ev in cal.list_events(s, e):
            print(f"  {ev.starts_at:%a %d %b %H:%M}-{ev.ends_at:%H:%M}  "
                  f"{ev.subject}  ({ev.organizer})")


def cmd_prefs(_a):
    with db.conn() as c:
        p = preferences.get(c)
    for k, v in p.items():
        print(f"  {k}: {v}")


def cmd_demo(a):
    project, hours = a.project, a.hours
    db.reset()
    with db.conn() as c:
        preferences.seed(c)
        ws = seed.seed_week(c)
        cfg, cal, tasks = _ctx(c)
        tz = ZoneInfo(cfg.timezone)

        hdr(1, "THE PRINCIPAL'S RULES (stored, versioned, his words)")
        p = preferences.get(c)
        print(f"  Working day        {p['workday']['start']}–{p['workday']['end']}  "
              f"({p['timezone']})")
        print(f"  No meetings before {p['no_meetings_before']}")
        print(f"  Buffer             {p['buffer_minutes']} min around every commitment")
        print(f"  Focus blocks       {p['min_focus_block_minutes']}–"
              f"{p['max_focus_block_minutes']} min, max "
              f"{p.get('max_focus_blocks_per_day', 1)}/day")
        for b in p["protected_blocks"]:
            print(f"  Protected          {b['label']}")

        hdr(2, f"THE WEEK AS IT STANDS — week of {ws:%d %b %Y}")
        s, e = planner.week_bounds(ws, tz)
        for ev in cal.list_events(s, e):
            print(f"  {ev.starts_at:%a %d %b %H:%M}-{ev.ends_at:%H:%M}  {ev.subject}"
                  f"   [{ev.organizer}]")

        hdr(3, f'CLAUDE ASKS: "give {project} {hours:g} hours this week"')
        plan = planner.propose(c, cal, project, hours, ws)
        print(f"  Plan {plan['plan_id']} created. No writes have occurred.")

        hdr(4, "DRY RUN — what would change, for approval")
        res = engine.apply_plan(c, cfg, cal, tasks, plan["plan_id"], dry_run=True)
        print(diff.render(res["plan"]))

        hdr(5, "APPROVED — applying for real")
        res = engine.apply_plan(c, cfg, cal, tasks, plan["plan_id"], dry_run=False)
        print(f"  {res['message']}")
        for x in res["applied"]:
            print(f"    applied  {x['action']:<13} {x['subject']}")
        for x in res["skipped"]:
            print(f"    skipped  {x['action'].get('subject', x['action'].get('name'))}"
                  f"  — {x['why']}")

        hdr(6, "THE CALENDAR NOW")
        for ev in cal.list_events(s, e):
            flag = "  <- agent" if (ev.organizer or "") == "agent" else ""
            print(f"  {ev.starts_at:%a %d %b %H:%M}-{ev.ends_at:%H:%M}  {ev.subject}{flag}")
        print("\n  ClickUp:")
        for t in tasks.list_tasks():
            print(f"    {t.name}  (due {t.due_at:%a %d %b %H:%M})")

        hdr(7, "IDEMPOTENCY — the same plan applied twice")
        again = engine.apply_plan(c, cfg, cal, tasks, plan["plan_id"], dry_run=False)
        print(f"  {again['message']}")
        print(f"  Nothing duplicated: {len(again['applied'])} new action(s), "
              f"{len(again['skipped'])} recognised as already done.")

        hdr(8, "AUDIT TRAIL — every action, before and after")
        for r in reversed(audit.tail(c, 40)):
            dr = "DRY " if r["dry_run"] else "    "
            tgt = f"{r['target_system'] or '-'}:{(r['target_id'] or '-')[:16]}"
            print(f"  #{r['id']:<3} {r['at']:%H:%M:%S} {dr}{r['actor']:<7} "
                  f"{r['tool']:<13} {r['result']:<8} {tgt}")
            if r["before_state"] and r["after_state"]:
                b, af = r["before_state"], r["after_state"]
                if b.get("starts_at") != af.get("starts_at"):
                    print(f"        before  {b['starts_at']}  ->  after  {af['starts_at']}")

        hdr(9, "HEALTH — all green")
        print(f"  {health.check(c, cal, tasks)}")

        hdr(10, "NOW BREAK IT ON PURPOSE — expire the calendar credential")
        set_fault(c, "calendar", "auth_expired")
        h = health.check(c, cal, tasks, webhook=cfg.alert_webhook)
        print(f"  health: {h['status']}, {h['failures']} failure(s)")
        try:
            planner.propose(c, cal, project, 2, ws)
        except Exception as ex:
            print(f"  planning refused rather than guessing: {type(ex).__name__}: {ex}")
        print("\n  Alerts raised:")
        for ev in health.recent(c, 5):
            if ev["level"] == "alert":
                print(f"    [{ev['level'].upper()}] {ev['at']:%H:%M:%S} {ev['message']}")
        print("\n  The failure is loud, logged and attributable — not silent.")

        hdr(11, "RECOVERY — credential restored, system resumes")
        set_fault(c, "calendar", "healthy")
        print(f"  {health.check(c, cal, tasks)}")


def cmd_export(a):
    from . import export_demo as _ed
    path = _ed.write(a.out)
    import json as _json
    n = len(_json.load(open(path))["audit"])
    print(f"wrote {path} — one real run, {n} audit entries")


def cmd_audit(a):
    with db.conn() as c:
        for r in reversed(audit.tail(c, a.limit)):
            print(f"#{r['id']:<4} {r['at']:%Y-%m-%d %H:%M:%S} dry={r['dry_run']!s:<5} "
                  f"{r['actor']:<8} {r['tool']:<14} {r['result']:<8} "
                  f"{r['target_system'] or '-'}:{r['target_id'] or '-'}")


def cmd_health(_a):
    with db.conn() as c:
        cfg, cal, tasks = _ctx(c)
        print(health.check(c, cal, tasks, webhook=cfg.alert_webhook))


def cmd_break(a):
    with db.conn() as c:
        set_fault(c, a.system, a.mode)
    print(f"fault_injection: {a.system} -> {a.mode}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gca", description="Governed Calendar Agent demo")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("reset").set_defaults(fn=cmd_reset)
    sub.add_parser("prefs").set_defaults(fn=cmd_prefs)
    sub.add_parser("week").set_defaults(fn=cmd_week)
    sub.add_parser("health").set_defaults(fn=cmd_health)

    d = sub.add_parser("demo", help="run the full scripted demo")
    d.add_argument("--project", default="Project Atlas")
    d.add_argument("--hours", type=float, default=4.0)
    d.set_defaults(fn=cmd_demo)

    ex = sub.add_parser("export", help="dump one real run to JSON for the demo page")
    ex.add_argument("--out", default="demo_run.json")
    ex.set_defaults(fn=cmd_export)

    au = sub.add_parser("audit")
    au.add_argument("--limit", type=int, default=40)
    au.set_defaults(fn=cmd_audit)

    br = sub.add_parser("break", help="inject a provider fault")
    br.add_argument("system", choices=["calendar", "tasks"])
    br.add_argument("mode", choices=["healthy", "auth_expired", "unreachable"])
    br.set_defaults(fn=cmd_break)

    args = ap.parse_args(argv)
    db.init()
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
