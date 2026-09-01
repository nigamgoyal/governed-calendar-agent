"""Microsoft Graph calendar provider (production path).

Scoping note, because this is the part that should be argued before it is coded:

  * App-only (client credentials) against Graph grants Calendars.ReadWrite
    tenant-wide by default. That is far more access than this agent needs.
  * The correct posture is an **ApplicationAccessPolicy** in Exchange Online
    restricting this app registration to the single governed mailbox. The agent
    then physically cannot read or write any other calendar in the tenant, and
    that constraint is enforced by Microsoft rather than by our code.
  * The principal's personal M365 is a separate tenant and needs its own app
    registration and its own consent. There is no single credential that spans
    both; multi-tenant here means two scoped identities, not one broad one.

Secrets are read from the environment and never logged. The audit trail records
which identity acted, never the credential itself.
"""
from datetime import datetime

import httpx

from .base import Event, ProviderAuthError, ProviderUnreachable

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"


class GraphCalendar:
    name = "outlook-graph"

    def __init__(self, cfg, client: httpx.Client | None = None):
        missing = [k for k in ("graph_tenant_id", "graph_client_id",
                               "graph_client_secret", "graph_user_id")
                   if not getattr(cfg, k)]
        if missing:
            raise ProviderAuthError(f"Graph config incomplete: {', '.join(missing)}")
        self.cfg = cfg
        self._client = client or httpx.Client(timeout=20.0)
        self._token: str | None = None

    # --- auth -------------------------------------------------------------
    def _access_token(self) -> str:
        if self._token:
            return self._token
        r = self._client.post(
            f"{LOGIN}/{self.cfg.graph_tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": self.cfg.graph_client_id,
                "client_secret": self.cfg.graph_client_secret,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
        )
        if r.status_code != 200:
            raise ProviderAuthError(f"token request failed: {r.status_code} {r.text[:200]}")
        self._token = r.json()["access_token"]
        return self._token

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        headers = kw.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._access_token()}"
        try:
            r = self._client.request(method, f"{GRAPH}{path}", headers=headers, **kw)
        except httpx.HTTPError as e:
            raise ProviderUnreachable(f"{self.name}: {e}") from e
        if r.status_code in (401, 403):
            self._token = None
            raise ProviderAuthError(f"{self.name}: {r.status_code} {r.text[:200]}")
        if r.status_code == 429:
            raise ProviderUnreachable(
                f"{self.name}: throttled, Retry-After={r.headers.get('Retry-After')}")
        if r.status_code >= 500:
            raise ProviderUnreachable(f"{self.name}: {r.status_code}")
        return r

    # --- calendar ---------------------------------------------------------
    def list_events(self, start: datetime, end: datetime) -> list[Event]:
        out, url = [], (
            f"/users/{self.cfg.graph_user_id}/calendarView"
            f"?startDateTime={start.isoformat()}&endDateTime={end.isoformat()}&$top=100"
        )
        while url:                                   # Graph paginates; follow @odata.nextLink
            r = self._req("GET", url)
            body = r.json()
            for e in body.get("value", []):
                out.append(self._event(e))
            nxt = body.get("@odata.nextLink")
            url = nxt.replace(GRAPH, "") if nxt else None
        return out

    def get_event(self, event_id: str) -> Event | None:
        r = self._req("GET", f"/users/{self.cfg.graph_user_id}/events/{event_id}")
        return self._event(r.json()) if r.status_code == 200 else None

    def create_event(self, subject, starts_at, ends_at, category=None) -> Event:
        payload = {
            "subject": subject,
            "start": {"dateTime": starts_at.isoformat(), "timeZone": self.cfg.timezone},
            "end": {"dateTime": ends_at.isoformat(), "timeZone": self.cfg.timezone},
        }
        if category:
            payload["categories"] = [category]
        r = self._req("POST", f"/users/{self.cfg.graph_user_id}/events", json=payload)
        return self._event(r.json())

    def move_event(self, event_id, starts_at, ends_at) -> Event:
        payload = {
            "start": {"dateTime": starts_at.isoformat(), "timeZone": self.cfg.timezone},
            "end": {"dateTime": ends_at.isoformat(), "timeZone": self.cfg.timezone},
        }
        r = self._req("PATCH", f"/users/{self.cfg.graph_user_id}/events/{event_id}",
                      json=payload)
        return self._event(r.json())

    def health(self) -> dict:
        try:
            self._req("GET", f"/users/{self.cfg.graph_user_id}?$select=id")
            return {"system": self.name, "status": "healthy"}
        except ProviderAuthError as e:
            return {"system": self.name, "status": "failed", "kind": "auth", "error": str(e)}
        except ProviderUnreachable as e:
            return {"system": self.name, "status": "failed", "kind": "unreachable",
                    "error": str(e)}

    @staticmethod
    def _event(e: dict) -> Event:
        return Event(
            id=e["id"],
            subject=e.get("subject", ""),
            starts_at=datetime.fromisoformat(e["start"]["dateTime"][:26]),
            ends_at=datetime.fromisoformat(e["end"]["dateTime"][:26]),
            organizer=(e.get("organizer") or {}).get("emailAddress", {}).get("address"),
            category=(e.get("categories") or [None])[0],
            etag=e.get("@odata.etag"),
        )
