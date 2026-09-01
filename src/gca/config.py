"""Configuration. Everything is env-driven so the same code runs against a
sandbox tenant (demo) or real Microsoft Graph / ClickUp (production).

Flipping from demo to production is a change of environment, not of code.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    dsn: str
    calendar_provider: str      # "sandbox" | "graph"
    tasks_provider: str         # "sandbox" | "clickup"
    timezone: str
    principal: str              # whose calendar this agent governs
    alert_webhook: str | None   # optional POST target for alerts

    # Microsoft Graph (multi-tenant: the principal's personal M365 + the org tenant)
    graph_tenant_id: str | None
    graph_client_id: str | None
    graph_client_secret: str | None
    graph_user_id: str | None

    # ClickUp
    clickup_token: str | None
    clickup_list_id: str | None


def load() -> Config:
    return Config(
        dsn=os.environ.get("GCA_DSN", "dbname=governed_calendar"),
        calendar_provider=os.environ.get("GCA_CALENDAR_PROVIDER", "sandbox"),
        tasks_provider=os.environ.get("GCA_TASKS_PROVIDER", "sandbox"),
        timezone=os.environ.get("GCA_TIMEZONE", "America/New_York"),
        principal=os.environ.get("GCA_PRINCIPAL", "principal@example.com"),
        alert_webhook=os.environ.get("GCA_ALERT_WEBHOOK") or None,
        graph_tenant_id=os.environ.get("GRAPH_TENANT_ID") or None,
        graph_client_id=os.environ.get("GRAPH_CLIENT_ID") or None,
        graph_client_secret=os.environ.get("GRAPH_CLIENT_SECRET") or None,
        graph_user_id=os.environ.get("GRAPH_USER_ID") or None,
        clickup_token=os.environ.get("CLICKUP_TOKEN") or None,
        clickup_list_id=os.environ.get("CLICKUP_LIST_ID") or None,
    )
