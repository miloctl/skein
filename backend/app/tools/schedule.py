"""Scheduling tools — thin wrappers over app.services.schedule."""

import json
from typing import Any

from strands import tool

from ..agents.identity import agent_identity
from ..services import schedule
from ._gate import gated_write


@tool
def schedule_event(
    title: str, starts_at: str, ends_at: str = "", description: str = "", attendees: str = ""
) -> str:
    """Add an event to the shared team calendar.

    Args:
        title: Event name.
        starts_at: Start time, ISO format (YYYY-MM-DDTHH:MM).
        ends_at: End time, ISO format, or empty.
        description: What the event is for.
        attendees: Comma-separated attendee names.
    """
    payload: dict[str, Any] = {
        "title": title,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "description": description,
        "attendees": attendees,
    }
    return gated_write(
        "event",
        "create",
        payload,
        lambda: schedule.schedule_event(**payload, actor=agent_identity(), origin="agent"),
    )


@tool
def list_events(from_date: str = "", limit: int = 25) -> str:
    """List upcoming calendar events, soonest first.

    Args:
        from_date: Only include events starting on/after this date (YYYY-MM-DD); empty for all.
        limit: Maximum number of events to return.
    """
    return json.dumps(schedule.list_events(from_date, limit))


@tool
def cancel_event(event_id: int) -> str:
    """Remove an event from the shared calendar. A hard delete, so it is
    ALWAYS a proposal for human review — like other destructive verbs.

    Args:
        event_id: ID of the event to cancel.
    """
    row = schedule.get_event(event_id)
    if not row:
        return json.dumps({"error": f"no event #{event_id}"})
    return gated_write(
        "event_cancel",
        "update",
        {},
        lambda: schedule.cancel_event(event_id, actor=agent_identity(), origin="agent"),
        entity_id=event_id,
        summary=f"cancel event #{event_id} '{row['title']}' ({row['starts_at']})",
    )
