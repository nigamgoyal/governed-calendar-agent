"""ClickUp task provider (production path).

Scoped to a single list id so the agent cannot create work outside the space it
was given. Rate limits (100 req/min on most plans) surface as ProviderUnreachable
so the caller retries rather than silently dropping the write.
"""
from datetime import datetime, timezone

import httpx

from .base import Task, ProviderAuthError, ProviderUnreachable

API = "https://api.clickup.com/api/v2"


class ClickUpTasks:
    name = "clickup"

    def __init__(self, cfg, client: httpx.Client | None = None):
        if not cfg.clickup_token or not cfg.clickup_list_id:
            raise ProviderAuthError("ClickUp config incomplete: token and list id required")
        self.cfg = cfg
        self._client = client or httpx.Client(
            timeout=20.0, headers={"Authorization": cfg.clickup_token})

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            r = self._client.request(method, f"{API}{path}", **kw)
        except httpx.HTTPError as e:
            raise ProviderUnreachable(f"{self.name}: {e}") from e
        if r.status_code in (401, 403):
            raise ProviderAuthError(f"{self.name}: {r.status_code} {r.text[:200]}")
        if r.status_code == 429:
            raise ProviderUnreachable(f"{self.name}: rate limited")
        if r.status_code >= 500:
            raise ProviderUnreachable(f"{self.name}: {r.status_code}")
        return r

    def list_tasks(self, project: str | None = None) -> list[Task]:
        r = self._req("GET", f"/list/{self.cfg.clickup_list_id}/task")
        tasks = [self._task(t) for t in r.json().get("tasks", [])]
        return [t for t in tasks if project is None or t.project == project]

    def create_task(self, name, due_at=None, project=None, description=None) -> Task:
        payload = {"name": name, "description": description or ""}
        if due_at:
            payload["due_date"] = int(due_at.timestamp() * 1000)
        r = self._req("POST", f"/list/{self.cfg.clickup_list_id}/task", json=payload)
        t = self._task(r.json())
        t.project = project
        return t

    def health(self) -> dict:
        try:
            self._req("GET", f"/list/{self.cfg.clickup_list_id}")
            return {"system": self.name, "status": "healthy"}
        except ProviderAuthError as e:
            return {"system": self.name, "status": "failed", "kind": "auth", "error": str(e)}
        except ProviderUnreachable as e:
            return {"system": self.name, "status": "failed", "kind": "unreachable",
                    "error": str(e)}

    @staticmethod
    def _task(t: dict) -> Task:
        due = t.get("due_date")
        return Task(
            id=t["id"],
            name=t.get("name", ""),
            status=(t.get("status") or {}).get("status", "to do"),
            due_at=datetime.fromtimestamp(int(due) / 1000, tz=timezone.utc) if due else None,
            description=t.get("description"),
        )
