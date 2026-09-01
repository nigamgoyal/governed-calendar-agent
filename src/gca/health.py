"""Health checks and alerting.

The failure this guards against is not a crash -- it is the silent one. A token
expires, writes stop landing, and nobody notices until a meeting is missed. Every
check result is persisted, and any failure raises an alert immediately.
"""
import json
import urllib.request
from psycopg2.extras import Json

from .db import cursor


def log_event(c, level: str, source: str, message: str, detail: dict | None = None) -> None:
    with cursor(c) as cur:
        cur.execute(
            "INSERT INTO system_events (level, source, message, detail) VALUES (%s,%s,%s,%s)",
            (level, source, message, Json(detail) if detail else None),
        )


def alert(c, source: str, message: str, detail: dict | None = None,
          webhook: str | None = None) -> None:
    log_event(c, "alert", source, message, detail)
    if webhook:
        try:
            req = urllib.request.Request(
                webhook,
                data=json.dumps({"source": source, "message": message,
                                 "detail": detail or {}}).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as e:                       # an alert must never crash the caller
            log_event(c, "warn", "alerting",
                      f"alert raised but webhook delivery failed: {e}")


def check(c, cal, tasks, webhook: str | None = None) -> dict:
    results = [cal.health(), tasks.health()]
    failed = [r for r in results if r["status"] != "healthy"]
    overall = "healthy" if not failed else "degraded"
    for r in failed:
        alert(c, r["system"],
              f"{r['system']} unhealthy ({r.get('kind', 'unknown')}): {r.get('error', '')}",
              detail=r, webhook=webhook)
    if not failed:
        log_event(c, "info", "health", "all systems healthy",
                  detail={"systems": [r["system"] for r in results]})
    return {"status": overall, "systems": results, "failures": len(failed)}


def recent(c, limit: int = 20) -> list[dict]:
    with cursor(c) as cur:
        cur.execute("SELECT * FROM system_events ORDER BY id DESC LIMIT %s", (limit,))
        return [dict(r) for r in cur.fetchall()]
