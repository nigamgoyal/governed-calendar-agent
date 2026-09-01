"""Append-only audit trail.

Two jobs:
  1. Record every action with enough before/after state that a non-technical
     principal, or a successor engineer, can reconstruct what happened.
  2. Provide the idempotency check that makes re-applying a plan a no-op
     instead of a duplicate booking.
"""
from psycopg2.extras import Json

from .db import cursor


def record(
    c,
    *,
    actor: str,
    tool: str,
    params: dict | None = None,
    dry_run: bool = False,
    target_system: str | None = None,
    target_id: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
    result: str = "ok",
    error: str | None = None,
    plan_id: str | None = None,
    idempotency_key: str | None = None,
) -> int | None:
    """Write one audit row. Returns the row id, or None if this exact action
    was already recorded (idempotency key collision)."""
    with cursor(c) as cur:
        cur.execute(
            """
            INSERT INTO audit_log (actor, tool, params, dry_run, target_system,
                                   target_id, before_state, after_state, result,
                                   error, plan_id, idempotency_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING id
            """,
            (actor, tool, Json(params) if params is not None else None, dry_run,
             target_system, target_id,
             Json(before) if before is not None else None,
             Json(after) if after is not None else None,
             result, error, plan_id, idempotency_key),
        )
        row = cur.fetchone()
        return row["id"] if row else None


def already_applied(c, idempotency_key: str) -> bool:
    with cursor(c) as cur:
        cur.execute(
            "SELECT 1 FROM audit_log WHERE idempotency_key = %s AND result = 'ok'",
            (idempotency_key,),
        )
        return cur.fetchone() is not None


def tail(c, limit: int = 50, plan_id: str | None = None) -> list[dict]:
    with cursor(c) as cur:
        if plan_id:
            cur.execute(
                "SELECT * FROM audit_log WHERE plan_id = %s ORDER BY id DESC LIMIT %s",
                (plan_id, limit),
            )
        else:
            cur.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT %s", (limit,))
        return [dict(r) for r in cur.fetchall()]
