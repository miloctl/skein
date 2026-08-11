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
_ENGAGEMENT_LINKED = frozenset(
    name for name, (_table, source) in _TABLES.items() if source == "engagement_id"
)


def existing(entity: str, entity_id: int) -> dict[str, str]:
    """Load classification and project type for an existing policy resource."""
    selected = _TABLES.get(entity)
    if selected is None or not entity_id:
        return {}
    table, project_source = selected
    if entity == "task":
        row = db.query_one(
            "SELECT task.visibility AS classification,"
            " task.crew_id AS crew_id,"
            " COALESCE(engagement.project_class, '') AS project_type"
            " FROM tasks task"
            " LEFT JOIN milestones milestone ON milestone.id = task.milestone_id"
            " LEFT JOIN engagements engagement"
            " ON engagement.id = COALESCE(task.engagement_id, milestone.engagement_id)"
            " WHERE task.id = ?",
            (entity_id,),
        )
    elif project_source == "project_class":
        row = db.query_one(
            f"SELECT visibility AS classification, crew_id,"  # noqa: S608 -- table comes from the closed map above
            " project_class AS project_type"
            f" FROM {table} WHERE id = ?",
            (entity_id,),
        )
    elif project_source:
        row = db.query_one(
            f"SELECT value.visibility AS classification, value.crew_id,"  # noqa: S608 -- identifiers come from the closed map above
            " COALESCE(engagement.project_class, '') AS project_type"
            f" FROM {table} value LEFT JOIN engagements engagement"
            f" ON engagement.id = value.{project_source} WHERE value.id = ?",
            (entity_id,),
        )
    else:
        row = db.query_one(
            f"SELECT visibility AS classification, crew_id, '' AS project_type"  # noqa: S608 -- table comes from the closed map above
            f" FROM {table} WHERE id = ?",
            (entity_id,),
        )
    return {key: str(value or "") for key, value in (row or {}).items()}


def _integer(value: object) -> int:
    if not isinstance(value, str | bytes | bytearray | int | float):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _engagement_project_type(engagement_id: int) -> str:
    if engagement_id <= 0:
        return ""
    row = db.query_one(
        "SELECT project_class FROM engagements WHERE id = ?",
        (engagement_id,),
    )
    return str((row or {}).get("project_class") or "")


def _milestone_engagement(milestone_id: int) -> int:
    if milestone_id <= 0:
        return 0
    row = db.query_one(
        "SELECT engagement_id FROM milestones WHERE id = ?",
        (milestone_id,),
    )
    return _integer((row or {}).get("engagement_id"))


def _named_engagement(name: object) -> int:
    if not str(name or ""):
        return 0
    row = db.query_one("SELECT id FROM engagements WHERE name = ?", (str(name),))
    return _integer((row or {}).get("id"))


def _target_engagement(entity: str, entity_id: int, payload: dict) -> int:
    """Resolve only relationship fields that the entity service persists."""
    existing_engagement = 0
    existing_milestone = 0
    if entity == "task" and entity_id:
        row = (
            db.query_one(
                "SELECT engagement_id, milestone_id FROM tasks WHERE id = ?",
                (entity_id,),
            )
            or {}
        )
        existing_engagement = _integer(row.get("engagement_id"))
        existing_milestone = _integer(row.get("milestone_id"))
    elif entity == "milestone" and entity_id:
        row = (
            db.query_one(
                "SELECT engagement_id FROM milestones WHERE id = ?",
                (entity_id,),
            )
            or {}
        )
        existing_engagement = _integer(row.get("engagement_id"))

    if entity in _ENGAGEMENT_LINKED and "engagement_id" in payload:
        value = _integer(payload.get("engagement_id"))
        if value != 0:
            existing_engagement = value if value > 0 else 0
    if entity == "task":
        if "milestone_id" in payload:
            value = _integer(payload.get("milestone_id"))
            if value != 0:
                existing_milestone = value if value > 0 else 0
        return existing_engagement or _milestone_engagement(existing_milestone)
    if entity == "milestone" and not entity_id and not existing_engagement:
        return _named_engagement(payload.get("project"))
    return existing_engagement


def for_change(entity: str, entity_id: int, payload: dict) -> dict[str, str]:
    """Return authoritative context for the state that one write would create."""
    if entity == "playbook":
        return playbook_context(str(payload.get("slug") or payload.get("playbook") or ""))
    selected = _TABLES.get(entity)
    if selected is None:
        return {}
    current = existing(entity, entity_id) if entity_id else {}
    classification = str(current.get("classification") or "")
    project_type = str(current.get("project_type") or "")

    # Core update services do not accept visibility. Therefore an ignored
    # extra JSON field cannot replace stored classification. Creates use the
    # public service default when callers omit the field.
    if not entity_id:
        classification = str(payload.get("visibility") or "workspace")
    if not entity_id and selected[1] == "project_class" and "project_class" in payload:
        project_type = str(payload.get("project_class") or "")
    elif selected[1] == "engagement_id":
        project_type = _engagement_project_type(_target_engagement(entity, entity_id, payload))
    return {"classification": classification, "project_type": project_type}


def proposed(entity: str, payload: dict) -> dict[str, str]:
    """Compatibility alias for authoritative create context."""
    return for_change(entity, 0, payload)


def for_route(resource_type: str, resource_id: str, payload: dict) -> dict[str, str]:
    """Return current and proposed non-content attributes for one REST route."""
    if resource_type == "playbooks":
        slug = str(payload.get("playbook") or resource_id or "")
        return playbook_context(slug)
    entity = _ROUTE_ENTITIES.get(resource_type)
    if entity is None:
        return {}
    try:
        entity_id = int(resource_id or 0)
    except ValueError:
        entity_id = 0
    return for_change(entity, entity_id, payload)


def playbook_context(slug: str, definition: dict | None = None) -> dict[str, str]:
    """Resolve one selected definition for REST, agent, and command policy."""
    # An invalid slug remains the entry point's validation error. Policy must
    # not turn it into a different response or trust a caller-supplied class.
    if not slug:
        return {}
    from . import playbooks

    if definition is None:
        try:
            definition = playbooks.get_playbook(slug)
        except ValueError:
            return {}
    return {
        "classification": "workspace",
        "project_type": str(definition.get("project_class") or slug),
        "definition_digest": playbooks.definition_digest(definition),
    }
