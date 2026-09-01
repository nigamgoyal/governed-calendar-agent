"""Sandbox tenant.

A persistent, Postgres-backed stand-in for Microsoft Graph and ClickUp with the
same call shapes, etag concurrency and failure modes. It exists so the trust
model -- dry run, audit, alerting -- can be exercised and demonstrated end to
end before anyone hands over a tenant.

Faults are injectable, so "the credential expired" is something we show, not
something we claim.
"""
import uuid
from datetime import datetime

from ..db import cursor
from .base import Event, Task, ProviderAuthError, ProviderUnreachable


def _fault(c, system: str) -> str:
    with cursor(c) as cur:
        cur.execute("SELECT mode FROM fault_injection WHERE system = %s", (system,))
        row = cur.fetchone()
        return row["mode"] if row else "healthy"


def set_fault(c, system: str, mode: str) -> None:
    with cursor(c) as cur:
        cur.execute(
            "INSERT INTO fault_injection (system, mode) VALUES (%s,%s) "
            "ON CONFLICT (system) DO UPDATE SET mode = EXCLUDED.mode",
            (system, mode),
        )


def _raise_for(mode: str, system: str) -> None:
    if mode == "auth_expired":
        raise ProviderAuthError(
            f"{system}: 401 InvalidAuthenticationToken — access token expired "
            f"or admin consent revoked"
        )
    if mode == "unreachable":
        raise ProviderUnreachable(f"{system}: endpoint unreachable after 3 retries")


class SandboxCalendar:
    name = "outlook-sandbox"

    def __init__(self, c):
        self.c = c

    def _guard(self):
        _raise_for(_fault(self.c, "calendar"), self.name)

    def list_events(self, start: datetime, end: datetime) -> list[Event]:
        self._guard()
        with cursor(self.c) as cur:
            cur.execute(
                "SELECT * FROM sandbox_calendar_events "
                "WHERE NOT deleted AND starts_at < %s AND ends_at > %s "
                "ORDER BY starts_at",
                (end, start),
            )
            return [self._row(r) for r in cur.fetchall()]

    def get_event(self, event_id: str) -> Event | None:
        self._guard()
        with cursor(self.c) as cur:
            cur.execute(
                "SELECT * FROM sandbox_calendar_events WHERE id = %s AND NOT deleted",
                (event_id,),
            )
            row = cur.fetchone()
            return self._row(row) if row else None

    def create_event(self, subject, starts_at, ends_at, category=None) -> Event:
        self._guard()
        eid = f"evt_{uuid.uuid4().hex[:12]}"
        etag = uuid.uuid4().hex[:8]
        with cursor(self.c) as cur:
            cur.execute(
                "INSERT INTO sandbox_calendar_events "
                "(id, subject, starts_at, ends_at, organizer, category, etag) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (eid, subject, starts_at, ends_at, "agent", category, etag),
            )
            return self._row(cur.fetchone())

    def move_event(self, event_id, starts_at, ends_at) -> Event:
        self._guard()
        etag = uuid.uuid4().hex[:8]
        with cursor(self.c) as cur:
            cur.execute(
                "UPDATE sandbox_calendar_events SET starts_at=%s, ends_at=%s, etag=%s "
                "WHERE id=%s AND NOT deleted RETURNING *",
                (starts_at, ends_at, etag, event_id),
            )
            row = cur.fetchone()
            if not row:
                raise ProviderUnreachable(f"{self.name}: event {event_id} not found")
            return self._row(row)

    def seed_event(self, subject, starts_at, ends_at, organizer="external", category=None):
        eid = f"evt_{uuid.uuid4().hex[:12]}"
        with cursor(self.c) as cur:
            cur.execute(
                "INSERT INTO sandbox_calendar_events "
                "(id, subject, starts_at, ends_at, organizer, category, etag) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (eid, subject, starts_at, ends_at, organizer, category,
                 uuid.uuid4().hex[:8]),
            )
            return self._row(cur.fetchone())

    def health(self) -> dict:
        try:
            self._guard()
            with cursor(self.c) as cur:
                cur.execute("SELECT count(*) AS n FROM sandbox_calendar_events WHERE NOT deleted")
                n = cur.fetchone()["n"]
            return {"system": self.name, "status": "healthy", "events": n}
        except (ProviderAuthError, ProviderUnreachable) as e:
            kind = "auth" if isinstance(e, ProviderAuthError) else "unreachable"
            return {"system": self.name, "status": "failed", "kind": kind, "error": str(e)}

    @staticmethod
    def _row(r) -> Event:
        return Event(id=r["id"], subject=r["subject"], starts_at=r["starts_at"],
                     ends_at=r["ends_at"], organizer=r["organizer"],
                     category=r["category"], etag=r["etag"])


class SandboxTasks:
    name = "clickup-sandbox"

    def __init__(self, c):
        self.c = c

    def _guard(self):
        _raise_for(_fault(self.c, "tasks"), self.name)

    def list_tasks(self, project: str | None = None) -> list[Task]:
        self._guard()
        with cursor(self.c) as cur:
            if project:
                cur.execute(
                    "SELECT * FROM sandbox_tasks WHERE NOT deleted AND project=%s "
                    "ORDER BY due_at NULLS LAST", (project,))
            else:
                cur.execute(
                    "SELECT * FROM sandbox_tasks WHERE NOT deleted ORDER BY due_at NULLS LAST")
            return [self._row(r) for r in cur.fetchall()]

    def create_task(self, name, due_at=None, project=None, description=None) -> Task:
        self._guard()
        tid = f"task_{uuid.uuid4().hex[:12]}"
        with cursor(self.c) as cur:
            cur.execute(
                "INSERT INTO sandbox_tasks (id, name, due_at, project, description) "
                "VALUES (%s,%s,%s,%s,%s) RETURNING *",
                (tid, name, due_at, project, description),
            )
            return self._row(cur.fetchone())

    def health(self) -> dict:
        try:
            self._guard()
            with cursor(self.c) as cur:
                cur.execute("SELECT count(*) AS n FROM sandbox_tasks WHERE NOT deleted")
                n = cur.fetchone()["n"]
            return {"system": self.name, "status": "healthy", "tasks": n}
        except (ProviderAuthError, ProviderUnreachable) as e:
            kind = "auth" if isinstance(e, ProviderAuthError) else "unreachable"
            return {"system": self.name, "status": "failed", "kind": kind, "error": str(e)}

    @staticmethod
    def _row(r) -> Task:
        return Task(id=r["id"], name=r["name"], status=r["status"], due_at=r["due_at"],
                    project=r["project"], description=r["description"])
