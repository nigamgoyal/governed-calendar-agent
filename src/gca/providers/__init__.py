from .base import (
    CalendarProvider, TaskProvider, ProviderAuthError, ProviderUnreachable, Event, Task,
)
from .sandbox import SandboxCalendar, SandboxTasks


def calendar(cfg, c):
    """Resolve the calendar backend from config. One env var separates the
    demo tenant from the principal's real Outlook."""
    if cfg.calendar_provider == "graph":
        from .graph import GraphCalendar
        return GraphCalendar(cfg)
    return SandboxCalendar(c)


def tasks(cfg, c):
    if cfg.tasks_provider == "clickup":
        from .clickup_api import ClickUpTasks
        return ClickUpTasks(cfg)
    return SandboxTasks(c)


__all__ = [
    "CalendarProvider", "TaskProvider", "ProviderAuthError", "ProviderUnreachable",
    "Event", "Task", "SandboxCalendar", "SandboxTasks", "calendar", "tasks",
]
