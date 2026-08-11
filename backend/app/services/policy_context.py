"""Non-content domain attributes for central policy decisions."""

from __future__ import annotations

from .. import db

_TABLES = {
    "task": ("tasks", "engagement_id"),
    "milestone": ("milestones", "engagement_id"),
    "question": ("questions", ""),
    "decision": ("decisions", ""),
    "note": ("notes", ""),
    "note_edit": ("notes", ""),
    "note_delete": ("notes", ""),
    "event": ("events", "engagement_id"),
    "event_cancel": ("events", "engagement_id"),
    "blocker": ("blockers", ""),
    "blocker_edit": ("blockers", ""),
    "promise": ("promises", "engagement_id"),
    "promise_edit": ("promises", "engagement_id"),
    "promise_settle": ("promises", "engagement_id"),
    "intake": ("intake_requests", "project_class"),
    "intake_edit": ("intake_requests", "project_class"),
    "memory": ("memories", "engagement_id"),
    "memory_forget": ("memories", "engagement_id"),
    "engagement": ("engagements", "project_class"),
}

_ROUTE_ENTITIES = {
    "tasks": "task",
    "milestones": "milestone",
    "questions": "question",
    "decisions": "decision",
    "notes": "note",
    "events": "event",
    "blockers": "blocker",
    "promises": "promise",
    "intake": "intake",
    "memories": "memory",
    "engagements": "engagement",
}


def existing(entity: str, entity_id: int) -> dict[str, str]:
    """Load classification and project type for an existing policy resource."""
    selected = _TABLES.get(entity)
    if selected is None or not entity_id:
        return {}
    table, project_source = selected
    if project_source == "project_class":
        row = db.query_one(
            f"SELECT visibility AS classification, project_class AS project_type"  # noqa: S608 -- table comes from the closed map above
            f" FROM {table} WHERE id = ?",
            (entity_id,),
        )
    elif project_source:
        row = db.query_one(
            f"SELECT value.visibility AS classification,"  # noqa: S608 -- identifiers come from the closed map above
            " COALESCE(engagement.project_class, '') AS project_type"
            f" FROM {table} value LEFT JOIN engagements engagement"
            f" ON engagement.id = value.{project_source} WHERE value.id = ?",
            (entity_id,),
        )
    else:
        row = db.query_one(
            f"SELECT visibility AS classification, '' AS project_type"  # noqa: S608 -- table comes from the closed map above
            f" FROM {table} WHERE id = ?",
            (entity_id,),
        )
    return {key: str(value or "") for key, value in (row or {}).items()}


def proposed(entity: str, payload: dict) -> dict[str, str]:
    """Resolve authoritative project data for a proposed create."""
    classification = str(payload.get("classification") or payload.get("visibility") or "")
    project_type = str(payload.get("project_type") or payload.get("project_class") or "")
    engagement_id = int(payload.get("engagement_id") or 0)
    if engagement_id:
        row = db.query_one(
            "SELECT project_class FROM engagements WHERE id = ?",
            (engagement_id,),
        )
        if row:
            project_type = str(row["project_class"] or "")
    return {"classification": classification, "project_type": project_type}


def for_route(resource_type: str, resource_id: str, payload: dict) -> dict[str, str]:
    """Return current and proposed non-content attributes for one REST route."""
    entity = _ROUTE_ENTITIES.get(resource_type)
    if entity is None:
        return {}
    attributes: dict[str, str] = {}
    try:
        entity_id = int(resource_id or 0)
    except ValueError:
        entity_id = 0
    if entity_id:
        attributes.update(existing(entity, entity_id))
    target = proposed(entity, payload)
    attributes.update({key: value for key, value in target.items() if value})
    return attributes
