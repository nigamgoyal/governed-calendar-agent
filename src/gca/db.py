"""Postgres schema + connection handling.

The audit log is the product, not a side effect: every state-changing call
records who asked, what was asked, the exact parameters, the before and after
state of the affected object, and the outcome.
"""
import json
import psycopg2
import psycopg2.extras
from contextlib import contextmanager

from . import config

psycopg2.extras.register_default_jsonb(globally=True, loads=json.loads)

SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
    id          int PRIMARY KEY DEFAULT 1,
    doc         jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT preferences_singleton CHECK (id = 1)
);

CREATE TABLE IF NOT EXISTS plans (
    plan_id     text PRIMARY KEY,
    created_at  timestamptz NOT NULL DEFAULT now(),
    intent      text NOT NULL,
    status      text NOT NULL DEFAULT 'proposed',   -- proposed | applied | superseded
    payload     jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              bigserial PRIMARY KEY,
    at              timestamptz NOT NULL DEFAULT now(),
    actor           text NOT NULL,
    tool            text NOT NULL,
    params          jsonb,
    dry_run         boolean NOT NULL DEFAULT false,
    target_system   text,
    target_id       text,
    before_state    jsonb,
    after_state     jsonb,
    result          text NOT NULL,          -- ok | skipped | error
    error           text,
    plan_id         text,
    idempotency_key text UNIQUE
);

CREATE TABLE IF NOT EXISTS system_events (
    id       bigserial PRIMARY KEY,
    at       timestamptz NOT NULL DEFAULT now(),
    level    text NOT NULL,                 -- info | warn | alert
    source   text NOT NULL,
    message  text NOT NULL,
    detail   jsonb
);

-- Sandbox tenant: a real, persistent stand-in for Microsoft Graph so the whole
-- system can be exercised end to end without a tenant. Same shape as Graph.
CREATE TABLE IF NOT EXISTS sandbox_calendar_events (
    id          text PRIMARY KEY,
    subject     text NOT NULL,
    starts_at   timestamptz NOT NULL,
    ends_at     timestamptz NOT NULL,
    organizer   text,
    category    text,
    etag        text NOT NULL,
    deleted     boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS sandbox_tasks (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    status      text NOT NULL DEFAULT 'to do',
    due_at      timestamptz,
    project     text,
    description text,
    deleted     boolean NOT NULL DEFAULT false
);

-- Injected-failure switchboard, so a broken credential can be demonstrated
-- deliberately rather than described.
CREATE TABLE IF NOT EXISTS fault_injection (
    system  text PRIMARY KEY,
    mode    text NOT NULL      -- healthy | auth_expired | unreachable
);
"""


@contextmanager
def conn(dsn: str | None = None):
    cfg = config.load()
    c = psycopg2.connect(dsn or cfg.dsn)
    c.autocommit = False
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def cursor(c):
    return c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init(dsn: str | None = None) -> None:
    with conn(dsn) as c:
        with c.cursor() as cur:
            cur.execute(SCHEMA)


def reset(dsn: str | None = None) -> None:
    """Wipe all state. Used by the demo driver and the tests."""
    with conn(dsn) as c:
        with c.cursor() as cur:
            cur.execute(SCHEMA)
            cur.execute(
                "TRUNCATE audit_log, plans, system_events, "
                "sandbox_calendar_events, sandbox_tasks, fault_injection, preferences"
            )
