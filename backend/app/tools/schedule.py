"""Scheduling tools — thin wrappers over app.services.schedule."""

import json
from typing import Any

from strands import tool

from .. import db
from ..agents.identity import agent_identity
from ..extensions.policy import (
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    current_policy_engine,
    current_policy_subject,
)
from ..services import policy_context, schedule, scope
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
    with db.read_transaction():
        rows = schedule.list_events(from_date, limit)
        contexts = policy_context.engagement_linked_collection_contexts("event", rows, scope.NOBODY)
        subject = current_policy_subject()
        engine = current_policy_engine()
        return json.dumps(
            [
                row
                for row in rows
                if engine.decide(
                    PolicyInput(
                        subject,
                        "skein.tool.list_events",
                        PolicyResource(
                            "event",
                            str(row["id"]),
                            contexts[int(row["id"])]["project_type"],
                            contexts[int(row["id"])]["classification"],
                            contexts[int(row["id"])],
                        ),
                        "agent_tool",
                        agent=agent_identity(),
                        tool="list_events",
                        tool_effect="read",
                        tool_risk="low",
                    )
                ).effect
                == PolicyEffect.PERMIT
            ]
        )


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
        # the time stays, the title does not: scope.detail keeps a scoped
        # event's name out of the review queue and its team notification
        summary=scope.detail(
            row["visibility"],
            f"cancel event #{event_id} ({row['starts_at']})",
            f"'{row['title']}'",
        ),
    )
