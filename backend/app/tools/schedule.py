"""Scheduling tools: shared team calendar events."""

import json

from strands import tool

from .. import db


@tool
def schedule_event(
    title: str,
    starts_at: str,
    ends_at: str = "",
    description: str = "",
    attendees: str = "",
) -> str:
    """Add an event to the shared team calendar.

    Args:
        title: Event name.
        starts_at: Start time, ISO format (YYYY-MM-DDTHH:MM).
        ends_at: End time, ISO format, or empty.
        description: What the event is for.
        attendees: Comma-separated attendee names.
    """
    eid = db.execute(
        "INSERT INTO events (title, description, starts_at, ends_at, attendees, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (title, description, starts_at, ends_at or None, attendees, db.now()),
    )
    db.log_activity("agent", "schedule_event", f"#{eid} {title} @ {starts_at}")
    return json.dumps({"id": eid, "title": title, "starts_at": starts_at})


@tool
def list_events(from_date: str = "", limit: int = 25) -> str:
    """List upcoming calendar events, soonest first.

    Args:
        from_date: Only include events starting on/after this date (YYYY-MM-DD); empty for all.
        limit: Maximum number of events to return.
    """
    if from_date:
        return json.dumps(db.query(
            "SELECT * FROM events WHERE starts_at >= ? ORDER BY starts_at LIMIT ?",
            (from_date, limit),
        ))
    return json.dumps(db.query("SELECT * FROM events ORDER BY starts_at LIMIT ?", (limit,)))


@tool
def cancel_event(event_id: int) -> str:
    """Remove an event from the shared calendar.

    Args:
        event_id: ID of the event to cancel.
    """
    db.execute("DELETE FROM events WHERE id = ?", (event_id,))
    db.log_activity("agent", "cancel_event", f"#{event_id}")
    return json.dumps({"id": event_id, "cancelled": True})
