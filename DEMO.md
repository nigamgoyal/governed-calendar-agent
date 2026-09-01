# The five-minute demo

`./run_demo.sh` runs all of it. What to say while it scrolls.

---

**Steps 1–2 — the rules, and the week.**
> "These are his rules, stored as data — no meetings before nine, fifteen minutes
> between commitments, Friday afternoon protected. And this is his week as it
> stands: two of these already break his own rules."

**Step 3 — the ask.**
> "In Claude, he says: give Project Atlas four hours this week. That's the whole
> interface."

**Step 4 — the dry run. Slow down here; this is the product.**
> "Nothing has been written. This is what *would* change. Two focus blocks placed
> in real gaps. One early meeting moved — that one's his own prep, so the agent
> moves it. And the Friday investor call is flagged, **not** moved, because an
> outside party organised it and moving it would email them in his name. An agent
> that quietly emails your investors is not one you can keep."

**Step 5–6 — approval and result.**
> "He approves. Now it writes: calendar and ClickUp, in one step, describing the
> same week."

**Step 7 — idempotency.**
> "Same plan applied again. Zero new actions. A retry after a timeout can't
> double-book him."

**Step 8 — the audit trail.**
> "Every action, with before and after state. When he asks in November why a
> meeting moved in August, there's an answer."

**Steps 9–11 — break it deliberately.**
> "Healthy. Now I expire the calendar credential — this is the failure that
> actually happens, months in, when a client secret quietly expires. Watch: the
> health check goes red, the alert fires, and the planner **refuses to plan**
> rather than guessing against stale data. It fails loudly, then recovers."

---

**Close:**
> "That's a sandbox tenant, so no one had to hand me a credential to see it work.
> Same code against real Outlook is one environment variable — the Graph adapter
> is in the repo. The runbook is there too: what breaks, why, and how to fix it,
> written so whoever comes after me doesn't need me."

## Honest caveats — say these before you're asked
- Sandbox tenant, not their Graph. The Graph adapter is written but unexercised.
- No event-driven sync loop yet; reconciliation is pull-based.
- Recurring events are treated as individual instances.
