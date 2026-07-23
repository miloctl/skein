"""Scheduling tools — thin wrappers over app.services.schedule."""

import json

from strands import tool

from ..services import schedule


@tool
def schedule_event(title: str, starts_at: str, ends_at: str = "",
                   description: str = "", attendees: str = "") -> str:
    """Add an event to the shared team calendar.

    Args:
        title: Event name.
        starts_at: Start time, ISO format (YYYY-MM-DDTHH:MM).
        ends_at: End time, ISO format, or empty.
        description: What the event is for.
        attendees: Comma-separated attendee names.
    """
    try:
        return json.dumps(schedule.schedule_event(title, starts_at, ends_at, description,
                                                  attendees, actor="agent", origin="agent"))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


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
    """Remove an event from the shared calendar.

    Args:
        event_id: ID of the event to cancel.
    """
    return json.dumps(schedule.cancel_event(event_id, actor="agent", origin="agent"))
