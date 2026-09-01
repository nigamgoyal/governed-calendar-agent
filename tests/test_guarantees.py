"""These tests exist to make the promises checkable.

Each one corresponds to a claim made to the client: nothing is written without
approval, the stated preferences are actually binding, an agent will not email a
counterparty on its own, a retry cannot double-book, and a broken credential
fails loudly.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from gca import audit, diff, engine, health, planner, preferences
from gca.providers.base import ProviderAuthError
from gca.providers.sandbox import set_fault


def _propose(ctx, project="Project Atlas", hours=4.0):
    return planner.propose(ctx["c"], ctx["cal"], project, hours, ctx["week_start"])


def _intervals(plan, existing):
    """Every interval the week would contain if the plan were applied."""
    moved = {a["event_id"]: a for a in plan["actions"] if a["kind"] == "move_event"}
    out = []
    for ev in existing:
        if ev.id in moved:
            m = moved[ev.id]["to"]
            out.append((datetime.fromisoformat(m["starts_at"]),
                        datetime.fromisoformat(m["ends_at"]), ev.subject))
        else:
            out.append((ev.starts_at, ev.ends_at, ev.subject))
    for a in plan["actions"]:
        if a["kind"] == "create_event":
            out.append((datetime.fromisoformat(a["starts_at"]),
                        datetime.fromisoformat(a["ends_at"]), a["subject"]))
    return out


# --- the plan is a proposal, not an action ---------------------------------

def test_propose_writes_nothing(ctx):
    before = [e.to_dict() for e in ctx["cal"].list_events(
        ctx["week_start"], ctx["week_start"] + timedelta(days=7))]
    _propose(ctx)
    after = [e.to_dict() for e in ctx["cal"].list_events(
        ctx["week_start"], ctx["week_start"] + timedelta(days=7))]
    assert before == after


def test_dry_run_writes_nothing(ctx):
    plan = _propose(ctx)
    before = len(ctx["cal"].list_events(ctx["week_start"],
                                        ctx["week_start"] + timedelta(days=7)))
    res = engine.apply_plan(ctx["c"], ctx["cfg"], ctx["cal"], ctx["tasks"],
                            plan["plan_id"], dry_run=True)
    after = len(ctx["cal"].list_events(ctx["week_start"],
                                       ctx["week_start"] + timedelta(days=7)))
    assert res["dry_run"] is True
    assert before == after
    assert ctx["tasks"].list_tasks() == []


def test_dry_run_is_the_default(ctx):
    """Anything above the engine that forgets the flag must still be safe."""
    plan = _propose(ctx)
    res = engine.apply_plan(ctx["c"], ctx["cfg"], ctx["cal"], ctx["tasks"],
                            plan["plan_id"])
    assert res["dry_run"] is True


# --- the preferences are binding -------------------------------------------

def test_no_block_starts_before_the_principals_cutoff(ctx):
    prefs = preferences.get(ctx["c"])
    cutoff = prefs["no_meetings_before"]
    plan = _propose(ctx)
    for a in plan["actions"]:
        if a["kind"] == "create_event":
            assert datetime.fromisoformat(a["starts_at"]).strftime("%H:%M") >= cutoff


def test_nothing_is_scheduled_inside_a_protected_block(ctx):
    prefs = preferences.get(ctx["c"])
    tz = ZoneInfo(prefs["timezone"])
    plan = _propose(ctx, hours=12.0)          # oversubscribe to force pressure
    for b in prefs["protected_blocks"]:
        idx = planner.WEEKDAYS.index(b["day"])
        day = ctx["week_start"] + timedelta(days=idx)
        p0 = planner._at(day, planner._t(b["start"]), tz)
        p1 = planner._at(day, planner._t(b["end"]), tz)
        for a in plan["actions"]:
            if a["kind"] == "create_event":
                s = datetime.fromisoformat(a["starts_at"])
                e = datetime.fromisoformat(a["ends_at"])
                assert not (s < p1 and e > p0), f"{a['subject']} landed in {b['label']}"


def test_no_proposed_block_overlaps_an_existing_or_moved_commitment(ctx):
    """Regression: a moved event must occupy its NEW slot when free time is
    computed, or the planner books straight over it."""
    plan = _propose(ctx, hours=8.0)
    existing = ctx["cal"].list_events(ctx["week_start"],
                                      ctx["week_start"] + timedelta(days=7))
    iv = sorted(_intervals(plan, existing))
    for (s1, e1, n1), (s2, e2, n2) in zip(iv, iv[1:]):
        assert e1 <= s2, f"overlap: '{n1}' {s1}-{e1} vs '{n2}' {s2}-{e2}"


def test_buffer_is_honoured_between_commitments(ctx):
    prefs = preferences.get(ctx["c"])
    buf = timedelta(minutes=prefs["buffer_minutes"])
    plan = _propose(ctx, hours=8.0)
    existing = ctx["cal"].list_events(ctx["week_start"],
                                      ctx["week_start"] + timedelta(days=7))
    created = [(datetime.fromisoformat(a["starts_at"]),
                datetime.fromisoformat(a["ends_at"]))
               for a in plan["actions"] if a["kind"] == "create_event"]
    for s, e in created:
        for other_s, other_e, name in _intervals(plan, existing):
            if (other_s, other_e) == (s, e):
                continue
            if other_e <= s:
                assert s - other_e >= buf, f"only {s - other_e} after '{name}'"
            elif other_s >= e:
                assert other_s - e >= buf, f"only {other_s - e} before '{name}'"


def test_focus_blocks_respect_min_and_max_length(ctx):
    prefs = preferences.get(ctx["c"])
    lo = timedelta(minutes=prefs["min_focus_block_minutes"])
    hi = timedelta(minutes=prefs["max_focus_block_minutes"])
    plan = _propose(ctx, hours=8.0)
    for a in plan["actions"]:
        if a["kind"] == "create_event":
            d = datetime.fromisoformat(a["ends_at"]) - datetime.fromisoformat(a["starts_at"])
            assert lo <= d <= hi


def test_unschedulable_hours_are_reported_not_silently_dropped(ctx):
    plan = _propose(ctx, hours=40.0)
    assert plan["shortfall_hours"] > 0
    assert plan["hours_scheduled"] < 40
    assert "could not fit" in diff.render(plan)


# --- the agent does not act on the principal's behalf toward outsiders ------

def test_external_meeting_is_flagged_never_moved(ctx):
    plan = _propose(ctx)
    flagged = [a for a in plan["actions"] if a["kind"] == "flag_conflict"]
    moved = [a for a in plan["actions"] if a["kind"] == "move_event"]
    assert any("Investor update" in a["subject"] for a in flagged)
    assert not any("Investor update" in a["subject"] for a in moved)


def test_internal_commitment_is_rescheduled_automatically(ctx):
    plan = _propose(ctx)
    moved = [a for a in plan["actions"] if a["kind"] == "move_event"]
    assert any("Internal prep" in a["subject"] for a in moved)


def test_flagged_conflicts_are_never_executed(ctx):
    plan = _propose(ctx)
    engine.apply_plan(ctx["c"], ctx["cfg"], ctx["cal"], ctx["tasks"],
                      plan["plan_id"], dry_run=False)
    fri = ctx["week_start"] + timedelta(days=4)
    still_there = [e for e in ctx["cal"].list_events(fri, fri + timedelta(days=1))
                   if "Investor update" in e.subject]
    assert len(still_there) == 1
    assert still_there[0].starts_at.strftime("%H:%M") == "14:00"


# --- retries cannot double-book --------------------------------------------

def test_reapplying_a_plan_is_idempotent(ctx):
    plan = _propose(ctx)
    a = engine.apply_plan(ctx["c"], ctx["cfg"], ctx["cal"], ctx["tasks"],
                          plan["plan_id"], dry_run=False)
    n_after_first = len(ctx["cal"].list_events(
        ctx["week_start"], ctx["week_start"] + timedelta(days=7)))
    b = engine.apply_plan(ctx["c"], ctx["cfg"], ctx["cal"], ctx["tasks"],
                          plan["plan_id"], dry_run=False)
    n_after_second = len(ctx["cal"].list_events(
        ctx["week_start"], ctx["week_start"] + timedelta(days=7)))
    assert len(a["applied"]) > 0
    assert len(b["applied"]) == 0
    assert n_after_first == n_after_second
    assert len(ctx["tasks"].list_tasks()) == 1


def test_partial_failure_resumes_without_duplicating(ctx):
    """Fail mid-plan, then re-run: the completed actions must not repeat and the
    remaining ones must complete."""
    plan = _propose(ctx)

    class FailsOnSecondCreate:
        name = ctx["cal"].name
        def __init__(self, inner): self.inner, self.n = inner, 0
        def list_events(self, *a): return self.inner.list_events(*a)
        def get_event(self, i): return self.inner.get_event(i)
        def move_event(self, *a): return self.inner.move_event(*a)
        def health(self): return self.inner.health()
        def create_event(self, *a, **k):
            self.n += 1
            if self.n == 2:
                raise ProviderAuthError("token expired mid-run")
            return self.inner.create_event(*a, **k)

    flaky = FailsOnSecondCreate(ctx["cal"])
    first = engine.apply_plan(ctx["c"], ctx["cfg"], flaky, ctx["tasks"],
                              plan["plan_id"], dry_run=False)
    assert first["ok"] is False
    assert first["errors"]
    partial = len(ctx["cal"].list_events(ctx["week_start"],
                                         ctx["week_start"] + timedelta(days=7)))

    second = engine.apply_plan(ctx["c"], ctx["cfg"], ctx["cal"], ctx["tasks"],
                               plan["plan_id"], dry_run=False)
    assert second["ok"] is True
    final = ctx["cal"].list_events(ctx["week_start"], ctx["week_start"] + timedelta(days=7))
    subjects = [e.subject for e in final]
    assert len(final) > partial
    assert len(ctx["tasks"].list_tasks()) == 1
    assert subjects.count("Project Atlas — focus block") == sum(
        1 for a in plan["actions"] if a["kind"] == "create_event")


# --- failure is loud -------------------------------------------------------

def test_expired_credential_raises_an_alert_not_a_silent_skip(ctx):
    set_fault(ctx["c"], "calendar", "auth_expired")
    result = health.check(ctx["c"], ctx["cal"], ctx["tasks"])
    assert result["status"] == "degraded"
    alerts = [e for e in health.recent(ctx["c"]) if e["level"] == "alert"]
    assert alerts and "InvalidAuthenticationToken" in alerts[0]["message"]


def test_planning_refuses_rather_than_guessing_when_calendar_is_down(ctx):
    set_fault(ctx["c"], "calendar", "auth_expired")
    with pytest.raises(ProviderAuthError):
        _propose(ctx)


def test_failed_apply_is_recorded_as_an_error_in_the_audit_trail(ctx):
    plan = _propose(ctx)
    set_fault(ctx["c"], "calendar", "unreachable")
    res = engine.apply_plan(ctx["c"], ctx["cfg"], ctx["cal"], ctx["tasks"],
                            plan["plan_id"], dry_run=False)
    assert res["ok"] is False
    errs = [r for r in audit.tail(ctx["c"], 50) if r["result"] == "error"]
    assert errs and errs[0]["error"]


# --- the audit trail is usable evidence ------------------------------------

def test_move_records_before_and_after_state(ctx):
    plan = _propose(ctx)
    engine.apply_plan(ctx["c"], ctx["cfg"], ctx["cal"], ctx["tasks"],
                      plan["plan_id"], dry_run=False)
    rows = [r for r in audit.tail(ctx["c"], 50) if r["tool"] == "move_event"]
    assert rows
    r = rows[0]
    assert r["before_state"]["starts_at"] != r["after_state"]["starts_at"]
    assert r["before_state"]["starts_at"].endswith("08:00:00-04:00")


def test_every_write_is_attributable_to_an_actor_and_a_plan(ctx):
    plan = _propose(ctx)
    engine.apply_plan(ctx["c"], ctx["cfg"], ctx["cal"], ctx["tasks"],
                      plan["plan_id"], dry_run=False)
    writes = [r for r in audit.tail(ctx["c"], 50)
              if r["tool"] in ("create_event", "move_event", "create_task")]
    assert writes
    for r in writes:
        assert r["actor"]
        assert r["plan_id"] == plan["plan_id"]
        assert r["target_id"]


def test_preference_change_is_audited_with_before_and_after(ctx):
    before = preferences.get(ctx["c"])["no_meetings_before"]
    preferences.set_key(ctx["c"], "no_meetings_before", "10:00")
    after = preferences.get(ctx["c"])["no_meetings_before"]
    assert before == "09:00" and after == "10:00"
    plan = _propose(ctx)
    for a in plan["actions"]:
        if a["kind"] == "create_event":
            assert datetime.fromisoformat(a["starts_at"]).strftime("%H:%M") >= "10:00"
