"""Provider contracts.

Both backends behind each contract are interchangeable, which is what lets the
demo run against a sandbox tenant while production runs against Microsoft Graph
and ClickUp with no change to the planner, the audit trail or the MCP tools.
"""
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Protocol


class ProviderAuthError(RuntimeError):
    """Credentials rejected — token expired, consent revoked, secret rotated."""


class ProviderUnreachable(RuntimeError):
    """Endpoint unreachable or erroring — network, outage, throttling."""


@dataclass
class Event:
    id: str
    subject: str
    starts_at: datetime
    ends_at: datetime
    organizer: str | None = None
    category: str | None = None
    etag: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["starts_at"] = self.starts_at.isoformat()
        d["ends_at"] = self.ends_at.isoformat()
        return d


@dataclass
class Task:
    id: str
    name: str
    status: str = "to do"
    due_at: datetime | None = None
    project: str | None = None
    description: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["due_at"] = self.due_at.isoformat() if self.due_at else None
        return d


class CalendarProvider(Protocol):
    name: str
    def list_events(self, start: datetime, end: datetime) -> list[Event]: ...
    def get_event(self, event_id: str) -> Event | None: ...
    def create_event(self, subject: str, starts_at: datetime, ends_at: datetime,
                     category: str | None = None) -> Event: ...
    def move_event(self, event_id: str, starts_at: datetime, ends_at: datetime) -> Event: ...
    def health(self) -> dict: ...


class TaskProvider(Protocol):
    name: str
    def list_tasks(self, project: str | None = None) -> list[Task]: ...
    def create_task(self, name: str, due_at: datetime | None = None,
                    project: str | None = None, description: str | None = None) -> Task: ...
    def health(self) -> dict: ...
