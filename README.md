# Governed Calendar Agent

A working proof of concept: **Claude given scoped, auditable write access to a
principal's calendar and project system.**

Built to answer one question — *how do you let an LLM touch a busy executive's
calendar without anyone having to trust it blindly?* The answer here is four
mechanisms, all demonstrable in about five minutes:

| Mechanism | What it means in practice |
|---|---|
| **Propose, don't act** | Claude produces a plan. Planning writes nothing, ever. |
| **Dry run as consent** | The plan renders as a plain-English diff. Approval is a separate, explicit step. |
| **Stated preferences are binding** | The principal's rules are data, and the planner cannot propose anything that violates them. |
| **Every action is evidence** | Who, what, exact parameters, before state, after state, outcome — in Postgres, append-only. |

Two rules do most of the work:

1. **The agent reschedules what it owns; it flags what it does not.** It will move
   an internal prep block. It will **not** move a meeting organised by an outside
   party, because that emails a counterparty in the principal's name. Those are
   surfaced for a human decision instead.
2. **Failure is loud.** A retry cannot double-book, a half-applied plan never
   reports success, and an expired credential raises an alert rather than
   quietly dropping writes.

---

## Run it

Requires Python 3.11+ and a local PostgreSQL.

```bash
./run_demo.sh
```

That resets the database, seeds a realistic week, and walks all eleven steps —
ending by deliberately expiring the calendar credential to show the alert fire.

```bash
PYTHONPATH=src ./.venv/bin/python -m pytest tests -q      # 20 tests
PYTHONPATH=src ./.venv/bin/python -m gca.cli week         # the seeded week
PYTHONPATH=src ./.venv/bin/python -m gca.cli audit        # the trail
PYTHONPATH=src ./.venv/bin/python -m gca.cli break calendar auth_expired
```

## Connect it to Claude

Copy `claude_desktop_config.example.json` into your Claude Desktop config,
replacing the two absolute paths. Then ask, in plain English:

> *"Give Project Atlas four hours this week."*

Claude calls `propose_plan`, shows you the diff, and waits. Nothing is written
until you say so.

---

## The MCP surface

Tool design **is** the governance boundary. Note what is deliberately absent:
no generic `run_sql`, no `graph_request`, no unscoped delete. Claude can only
express intents the server already knows how to make safe.

| Tool | Writes? | Purpose |
|---|---|---|
| `get_week` | no | Calendar + tasks for a week |
| `get_preferences` | no | The principal's stored rules |
| `propose_plan` | **no** | Turns an intent into a reviewable plan |
| `apply_plan` | only with `dry_run=false` | Executes an approved plan, idempotently |
| `get_audit_trail` | no | Who did what, before and after |
| `health_check` | no | Every connected system; alerts on failure |
| `set_preference` | config only | Change a rule, fully audited |

## Architecture

```
Claude  ──MCP/stdio──▶  server.py          seven scoped tools, dry-run by default
                            │
                            ├─▶ planner.py     intent ─▶ plan. READ ONLY.
                            │      └── preferences.py   the binding rules
                            ├─▶ diff.py        plan ─▶ a diff a human can approve
                            ├─▶ engine.py      the ONLY path that mutates anything
                            │      ├── audit.py    before/after + idempotency keys
                            │      └── health.py   alerting on any provider failure
                            └─▶ providers/
                                   ├── sandbox.py       persistent demo tenant
                                   ├── graph.py         Microsoft Graph (Outlook)
                                   └── clickup_api.py   ClickUp
                                              │
                                        PostgreSQL
                    preferences · plans · audit_log · system_events
```

Providers sit behind one contract, so the demo tenant and the principal's real
Outlook are interchangeable. **Going to production is an environment change, not
a rewrite:** set `GCA_CALENDAR_PROVIDER=graph` and supply credentials.

## What this POC does not yet do

Stated plainly, because a POC that pretends to be a product is worse than no POC:

- No event-driven sync loop yet (n8n / Power Automate). Reconciliation here is
  pull-based and manual.
- No OAuth device-code flow for the personal-tenant consent journey.
- Recurring events are treated as individual instances.
- The alert sink is a webhook. Production wants a real on-call path.
- The sandbox provider models auth failure, unreachability and etags, but not
  Graph's full concurrency semantics.

## Documentation

- `RUNBOOK.md` — operating, diagnosing and taking this over
- `DEMO.md` — the five-minute script
