# Runbook

Written to the standard the brief actually asked for: **enough for a successor
engineer to take ownership without a handover conversation.** If anything here
is not sufficient to do that, it is a defect in this file, not in the reader.

---

## 1. What this system is responsible for

Giving Claude scoped write access to one principal's Outlook calendar and one
ClickUp list, such that every change is proposed before it is made, constrained
by stored preferences, and recorded afterwards.

**If this system is entirely down**, nothing is lost and nothing corrupts. The
calendar simply stops being managed by the agent. There is no queue that drains
incorrectly and no state that drifts. Degrading to "a human does it this week"
is always safe — a deliberate design property, not luck.

## 2. Where state lives

All of it in PostgreSQL (`GCA_DSN`). There is no other durable state.

| Table | Holds | Safe to truncate? |
|---|---|---|
| `preferences` | The principal's rules, one JSON doc | No — recreate from `preferences.DEFAULT` |
| `plans` | Every proposed plan, applied or not | Yes, after 90 days |
| `audit_log` | Append-only record of every action | **No.** This is the evidence. |
| `system_events` | Health checks and alerts | Yes, after 30 days |
| `sandbox_*` | Demo tenant only | Yes, always |
| `fault_injection` | Demo fault switches | Yes, always |

`audit_log.idempotency_key` is what makes retries safe. Deleting rows from
`audit_log` can cause a re-applied plan to double-book. Do not.

## 3. Configuration

Everything is environment-driven; see `.env.example`. The two that change
behaviour most:

- `GCA_CALENDAR_PROVIDER` — `sandbox` (demo) or `graph` (real Outlook)
- `GCA_TASKS_PROVIDER` — `sandbox` or `clickup`

## 4. Access model — read this before touching credentials

**Two tenants, two identities, never one.** The organisation's M365 and the
principal's personal M365 are separate tenants. Each needs its own app
registration and its own admin consent. There is no credential spanning both,
and anyone who offers you one has misunderstood the requirement.

**Constrain the app, not just the code.** App-only Graph access grants
`Calendars.ReadWrite` across the whole tenant by default — vastly more than this
agent needs. The correct posture is an Exchange Online
**ApplicationAccessPolicy** limiting the registration to the single governed
mailbox. Then the agent *cannot* read another person's calendar even if this
code is wrong. Enforce at the platform, verify in the code.

ClickUp is scoped by list id. Widening it is a deliberate act, not a default.

Secrets come from the environment and are never logged. The audit trail records
which identity acted, never the credential.

## 5. Routine operations

```bash
# is everything alive?
PYTHONPATH=src ./.venv/bin/python -m gca.cli health

# what has the agent been doing?
PYTHONPATH=src ./.venv/bin/python -m gca.cli audit --limit 50

# what does it believe the principal's rules are?
PYTHONPATH=src ./.venv/bin/python -m gca.cli prefs

# full regression suite before any change ships
PYTHONPATH=src ./.venv/bin/python -m pytest tests -q
```

## 6. Diagnosing the failures you will actually see

### "Claude says it scheduled something, but the calendar is unchanged"
Almost always `apply_plan` was called with `dry_run=true` (the default).
Check: `SELECT tool, dry_run, result FROM audit_log ORDER BY id DESC LIMIT 10;`
A dry run is recorded with `dry_run = true`. Nothing was written; re-apply.

### `ProviderAuthError: 401 InvalidAuthenticationToken`
The token expired, the client secret rotated, or admin consent was revoked.
Client secrets expire — **this is the single most likely cause of a silent
outage months from now.** Check the app registration's secret expiry first.
Recovery: replace `GRAPH_CLIENT_SECRET`, restart, run `health`. No data repair
is needed; nothing is half-written.

### `ProviderUnreachable: throttled`
Graph rate limiting. Honour `Retry-After`. If it is persistent, the calendar
window being requested is too wide — narrow it.

### An apply stopped part-way through
Expected and safe. The run halts on the first provider error rather than
continuing, and the completed actions carry idempotency keys. **Fix the cause,
then re-apply the same plan id.** Completed actions are skipped; the rest
finish. Never hand-edit the calendar to "catch up" — that is how the audit trail
stops matching reality.

### A conflict keeps reappearing
Conflicts organised by an outside party are flagged, never resolved. That is
correct. Someone has to decide.

## 7. Changing the principal's preferences

Preferred: `set_preference` through Claude, so the change is audited.
Direct: edit the `preferences.doc` JSON. Always re-run the test suite —
several tests assert the rules are actually binding.

Adding a *new kind* of rule means changing `planner.propose`, and it must come
with a test proving the planner cannot violate it. That is the bar for this
codebase.

## 8. Deploying

Stateless apart from Postgres, so it deploys anywhere that runs Python with a
managed database. Requirements: a managed Postgres with backups, environment
secrets from a real secret store rather than a `.env` file, an alert webhook
pointing at a channel a human reads, and a scheduled `health_check`.

Restarting is always safe. There is no in-memory state worth preserving.

## 9. Extending it — the invariants to preserve

Break any of these and the system stops being trustworthy, whatever else it does:

1. **The planner never writes.** If it needs to write, the design is wrong.
2. **`engine.apply_plan` is the only mutation path**, and every action through
   it carries an idempotency key.
3. **Every write produces an audit row**, with before and after state.
4. **`dry_run` defaults to `True`** at every layer.
5. **No tool accepts free-form queries or arbitrary API paths.** The tool list is
   the permission boundary; a generic escape hatch dissolves it.
6. **The agent never contacts a third party on the principal's behalf** without
   explicit human approval of that specific action.
