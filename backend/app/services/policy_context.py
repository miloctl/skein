"""Non-content domain attributes for central policy decisions."""

from __future__ import annotations

from collections.abc import Callable

from .. import db
from . import scope

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
    "lesson": ("lessons", "engagement_id"),
    "allocation": ("allocations", "engagement_id"),
    "artifact": ("artifacts", "engagement_id"),
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
    "lessons": "lesson",
    "allocations": "allocation",
    "artifacts": "artifact",
}
_ENGAGEMENT_LINKED = frozenset(
    name for name, (_table, source) in _TABLES.items() if source == "engagement_id"
)
_UNSCOPED_RESOURCES = {"finding": "findings"}


def supports_resource(entity: str) -> bool:
    """Return true when the central resolver knows this domain resource."""
    return entity in _TABLES or entity in _UNSCOPED_RESOURCES


def engagement_linked_collection_contexts(
    entity: str,
    rows: list[dict],
    viewer: scope.Viewer,
) -> dict[int, dict[str, str]]:
    """Resolve one visible collection's project links without hidden-row oracles."""
    selected = _TABLES.get(entity)
    if selected is None or selected[1] != "engagement_id":
        raise ValueError(f"{entity} does not have an engagement-linked policy context")
    row_ids = sorted({int(row["id"]) for row in rows})
    if not row_ids:
        return {}
    table = selected[0]
    marks = ",".join("?" for _ in row_ids)
    project_column = ", project_class" if entity == "lesson" else ""
    raw_rows = db.query(
        f"SELECT id, engagement_id{project_column} FROM {table} WHERE id IN ({marks})",  # noqa: S608 -- closed table map and controlled marks
        tuple(row_ids),
    )
    raw_links = {int(row["id"]): int(row.get("engagement_id") or 0) for row in raw_rows}
    standalone_projects = {
        int(row["id"]): str(row.get("project_class") or "")
        for row in raw_rows
        if entity == "lesson" and not row.get("engagement_id")
    }
    engagement_ids = sorted(set(raw_links.values()) - {0})
    projects: dict[int, str] = {}
    if engagement_ids:
        engagement_marks = ",".join("?" for _ in engagement_ids)
        visible, params = scope.visible_filter(viewer, "engagements", "engagement")
        projects = {
            int(row["id"]): str(row.get("project_class") or "")
            for row in db.query(
                f"SELECT engagement.id, engagement.project_class FROM engagements engagement"  # noqa: S608 -- controlled marks and scope
                f" WHERE engagement.id IN ({engagement_marks}) AND {visible}",
                (*engagement_ids, *params),
            )
        }
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        row_id = int(row["id"])
        engagement_id = raw_links.get(row_id, 0)
        attributes = {
            "classification": str(row.get("visibility") or ""),
            "project_type": projects.get(engagement_id, "") or standalone_projects.get(row_id, ""),
        }
        if engagement_id and engagement_id not in projects:
            attributes["relationship_conflict"] = "true"
        result[row_id] = attributes
    return result


def existing(entity: str, entity_id: int) -> dict[str, str]:
    """Load classification and project type for an existing policy resource."""
    if entity in _UNSCOPED_RESOURCES and entity_id:
        row = db.query_one(
            f"SELECT id FROM {_UNSCOPED_RESOURCES[entity]} WHERE id = ?",  # noqa: S608 -- closed table map
            (entity_id,),
        )
        return {"classification": "workspace", "project_type": ""} if row else {}
    selected = _TABLES.get(entity)
    if selected is None or not entity_id:
        return {}
    table, project_source = selected
    if entity == "allocation":
        row = db.query_one(
            "SELECT engagement.visibility AS classification, engagement.crew_id,"
            " engagement.project_class AS project_type"
            " FROM allocations value JOIN engagements engagement"
            " ON engagement.id = value.engagement_id WHERE value.id = ?",
            (entity_id,),
        )
        return {key: str(value or "") for key, value in (row or {}).items()}
    if entity == "task":
        return _task_context(entity_id, {})
    if entity == "milestone":
        row = db.query_one(
            "SELECT milestone.visibility AS classification, milestone.crew_id,"
            " engagement.project_class AS project_type,"
            " engagement.visibility AS engagement_visibility,"
            " engagement.crew_id AS engagement_crew_id"
            " FROM milestones milestone LEFT JOIN engagements engagement"
            " ON engagement.id = milestone.engagement_id WHERE milestone.id = ?",
            (entity_id,),
        )
        result = {key: str(value or "") for key, value in (row or {}).items()}
        if (
            row
            and row.get("engagement_visibility")
            and not scope.relationship_contains(
                str(row["engagement_visibility"]),
                row["engagement_crew_id"],
                str(row["classification"]),
                row["crew_id"],
            )
        ):
            result["relationship_conflict"] = "true"
            result["project_type"] = ""
        if db.query_one(
            "SELECT 1 AS conflict FROM tasks task JOIN milestones milestone"
            " ON milestone.id = task.milestone_id"
            " WHERE milestone.id = ? AND task.engagement_id IS NOT NULL"
            " AND milestone.engagement_id IS NOT NULL"
            " AND task.engagement_id != milestone.engagement_id LIMIT 1",
            (entity_id,),
        ):
            result["relationship_conflict"] = "true"
            result["project_type"] = ""
        result.pop("engagement_visibility", None)
        result.pop("engagement_crew_id", None)
        return result
    elif project_source == "project_class":
        row = db.query_one(
            f"SELECT visibility AS classification, crew_id,"  # noqa: S608 -- table comes from the closed map above
            " project_class AS project_type"
            f" FROM {table} WHERE id = ?",
            (entity_id,),
        )
    elif project_source:
        project_expression = (
            "COALESCE(engagement.project_class, value.project_class, '')"
            if entity == "lesson"
            else "COALESCE(engagement.project_class, '')"
        )
        row = db.query_one(
            f"SELECT value.visibility AS classification, value.crew_id,"  # noqa: S608 -- identifiers come from the closed map above
            f" {project_expression} AS project_type"
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


def _task_context(entity_id: int, payload: dict) -> dict[str, str]:
    row = db.query_one(
        "SELECT task.visibility AS classification, task.crew_id,"
        " task.engagement_id, task.milestone_id, milestone.engagement_id"
        " AS milestone_engagement_id"
        " FROM tasks task LEFT JOIN milestones milestone ON milestone.id = task.milestone_id"
        " WHERE task.id = ?",
        (entity_id,),
    )
    if row is None:
        return {}
    engagement_id = _integer(row.get("engagement_id"))
    milestone_id = _integer(row.get("milestone_id"))
    if "engagement_id" in payload:
        requested = _integer(payload.get("engagement_id"))
        if requested:
            engagement_id = max(requested, 0)
    if "milestone_id" in payload:
        requested = _integer(payload.get("milestone_id"))
        if requested:
            milestone_id = max(requested, 0)
    milestone_engagement_id = _milestone_engagement(milestone_id)
    conflict = bool(
        engagement_id and milestone_engagement_id and engagement_id != milestone_engagement_id
    )
    target = 0 if conflict else engagement_id or milestone_engagement_id
    result = {
        "classification": str(row.get("classification") or ""),
        "crew_id": str(row.get("crew_id") or ""),
        "project_type": _engagement_project_type(target),
    }
    if conflict:
        result["relationship_conflict"] = "true"
    return result


def _blocker_context(entity_id: int, payload: dict, *, actor: str = "") -> dict[str, str]:
    """Resolve a blocker's project through its linked task for review refresh."""
    row = None
    if entity_id:
        row = db.query_one(
            "SELECT visibility, crew_id, task_id FROM blockers WHERE id = ?",
            (entity_id,),
        )
        if row is None:
            return {}
    task_id = (
        _integer(payload.get("task_id")) if not entity_id else _integer((row or {}).get("task_id"))
    )
    result = {
        "classification": str(
            (row or {}).get("visibility") or payload.get("visibility") or scope.WORKSPACE
        ),
        "crew_id": str((row or {}).get("crew_id") or payload.get("crew_id") or ""),
        "project_type": "",
    }
    if task_id:
        if actor:
            from . import work

            viewer = scope.Viewer.for_actor(actor)
            try:
                row = work.get_task(task_id, viewer)
                task = work.task_read_policy_context(row, viewer)
            except (db.NotFound, ValueError):
                result["relationship_conflict"] = "true"
                return result
        else:
            task = _task_context(task_id, {})
        result["project_type"] = str(task.get("project_type") or "")
        if task.get("relationship_conflict"):
            result["relationship_conflict"] = "true"
    return result


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


def for_change(
    entity: str,
    entity_id: int,
    payload: dict,
    *,
    actor: str = "",
) -> dict[str, str]:
    """Return authoritative context for the state that one write would create."""
    if entity == "playbook":
        return playbook_context(str(payload.get("slug") or payload.get("playbook") or ""))
    if entity == "delegation":
        task_id = _integer(payload.get("task_id"))
        if not task_id:
            return {"classification": "", "project_type": ""}
        if actor:
            from . import work

            viewer = scope.Viewer.for_actor(actor)
            try:
                task = work.get_task(task_id, viewer)
            except (db.NotFound, ValueError):
                return {
                    "classification": "",
                    "project_type": "",
                    "relationship_conflict": "true",
                }
            return work.task_read_policy_context(task, viewer)
        return _task_context(task_id, {})
    selected = _TABLES.get(entity)
    if selected is None:
        return {}
    if entity in {"blocker", "blocker_edit"}:
        return _blocker_context(entity_id, payload, actor=actor)
    if entity == "lesson" and not entity_id:
        engagement_id = _integer(payload.get("engagement_id"))
        if engagement_id:
            project_type = (
                _visible_engagement_project_type(
                    engagement_id,
                    scope.Viewer.for_actor(actor),
                )
                if actor
                else _engagement_project_type(engagement_id)
            )
            if not project_type:
                return {
                    "classification": str(payload.get("visibility") or scope.WORKSPACE),
                    "project_type": "",
                    "relationship_conflict": "true",
                }
        else:
            project_type = str(payload.get("project_class") or "general")
        return {
            "classification": str(payload.get("visibility") or scope.WORKSPACE),
            "project_type": project_type,
        }
    if entity == "task":
        if actor:
            from . import work

            if entity_id:
                return work.task_update_policy_context(entity_id, payload, actor=actor)
            return work.task_create_policy_context(
                milestone_id=_integer(payload.get("milestone_id")),
                engagement_id=_integer(payload.get("engagement_id")),
                visibility=str(payload.get("visibility") or scope.WORKSPACE),
                crew_id=_integer(payload.get("crew_id")),
                actor=actor,
            )
        if entity_id:
            return _task_context(entity_id, payload)
    viewer = scope.Viewer.for_actor(actor) if actor else scope.NOBODY
    current = (
        existing_scoped(entity, entity_id, viewer)
        if actor and entity_id
        else existing(entity, entity_id)
        if entity_id
        else {}
    )
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
        target = (
            _target_engagement_scoped(entity, entity_id, payload, viewer)
            if actor
            else _target_engagement(entity, entity_id, payload)
        )
        project_type = (
            _visible_engagement_project_type(target, viewer)
            if actor
            else _engagement_project_type(target)
        )
    result = dict(current)
    result.update({"classification": classification, "project_type": project_type})
    if actor and selected[1] == "engagement_id" and target and not project_type:
        result["relationship_conflict"] = "true"
    return result


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


def _visible_engagement_project_type(engagement_id: int, viewer: scope.Viewer) -> str:
    if engagement_id <= 0:
        return ""
    visible, params = scope.visible_filter(viewer, "engagements", "engagement")
    row = db.query_one(
        f"SELECT engagement.project_class FROM engagements engagement"  # noqa: S608 -- scope emits bound marks
        f" WHERE engagement.id = ? AND {visible}",
        (engagement_id, *params),
    )
    return str((row or {}).get("project_class") or "")


def existing_scoped(entity: str, entity_id: int, viewer: scope.Viewer) -> dict[str, str]:
    """Load policy metadata only when the viewer can read the resource."""
    if entity in _UNSCOPED_RESOURCES:
        return existing(entity, entity_id)
    if entity == "task":
        from . import work

        try:
            task = work.get_task(entity_id, viewer)
        except (db.NotFound, ValueError):
            return {}
        return work.task_read_policy_context(task, viewer)
    selected = _TABLES.get(entity)
    if selected is None or not entity_id:
        return {}
    table, project_source = selected
    if entity == "allocation":
        visible, params = scope.visible_filter(viewer, "engagements", "engagement")
        row = db.query_one(
            f"SELECT engagement.visibility AS classification, engagement.crew_id,"  # noqa: S608 -- scope emits bound marks
            " engagement.project_class AS project_type"
            " FROM allocations value JOIN engagements engagement"
            " ON engagement.id = value.engagement_id"
            f" WHERE value.id = ? AND {visible}",
            (entity_id, *params),
        )
        return {key: str(value or "") for key, value in (row or {}).items()}
    visible, params = scope.visible_filter(viewer, table, "value")
    if project_source == "project_class":
        row = db.query_one(
            f"SELECT value.visibility AS classification, value.crew_id,"  # noqa: S608 -- closed table map and scope marks
            " value.project_class AS project_type"
            f" FROM {table} value WHERE value.id = ? AND {visible}",
            (entity_id, *params),
        )
    elif project_source:
        engagement_visible, engagement_params = scope.visible_filter(
            viewer, "engagements", "engagement"
        )
        project_expression = (
            "COALESCE(engagement.project_class, value.project_class, '')"
            if entity == "lesson"
            else "COALESCE(engagement.project_class, '')"
        )
        row = db.query_one(
            f"SELECT value.visibility AS classification, value.crew_id,"  # noqa: S608 -- closed table map and scope marks
            f" value.{project_source} AS relationship_id,"
            " engagement.id AS visible_relationship_id,"
            f" {project_expression} AS project_type"
            f" FROM {table} value LEFT JOIN engagements engagement"
            f" ON engagement.id = value.{project_source} AND {engagement_visible}"
            f" WHERE value.id = ? AND {visible}",
            (*engagement_params, entity_id, *params),
        )
    else:
        row = db.query_one(
            f"SELECT value.visibility AS classification, value.crew_id,"  # noqa: S608 -- closed table map and scope marks
            " '' AS project_type"
            f" FROM {table} value WHERE value.id = ? AND {visible}",
            (entity_id, *params),
        )
    result = {key: str(value or "") for key, value in (row or {}).items()}
    if project_source and project_source != "project_class":
        if result.get("relationship_id") and not result.get("visible_relationship_id"):
            result["project_type"] = ""
            result["relationship_conflict"] = "true"
        result.pop("relationship_id", None)
        result.pop("visible_relationship_id", None)
    return result


def resource_contexts(
    resources: list[tuple[str, int]],
    viewer: scope.Viewer,
) -> dict[tuple[str, int], dict[str, str]]:
    """Resolve visible search/composite resources through their domain parents."""
    result: dict[tuple[str, int], dict[str, str]] = {}
    grouped: dict[str, set[int]] = {}
    for entity, entity_id in resources:
        if entity_id > 0:
            grouped.setdefault(entity, set()).add(entity_id)
    for entity, ids in grouped.items():
        if entity == "task":
            from . import work

            marks = ",".join("?" for _ in ids)
            visible, params = scope.visible_filter(viewer, "tasks", "task")
            rows = db.query(
                f"SELECT task.* FROM tasks task WHERE task.id IN ({marks})"  # noqa: S608 -- controlled marks and scope
                f" AND {visible}",
                (*sorted(ids), *params),
            )
            contexts = work.task_collection_policy_contexts(rows, viewer)
            for row in rows:
                key = (entity, int(row["id"]))
                result[key] = contexts[int(row["id"])]
            continue
        if entity == "allocation":
            for entity_id in ids:
                context = existing_scoped(entity, entity_id, viewer)
                if context:
                    result[(entity, entity_id)] = context
            continue
        if entity == "blocker":
            from . import blockers

            marks = ",".join("?" for _ in ids)
            visible, params = scope.visible_filter(viewer, "blockers", "blocker")
            rows = db.query(
                f"SELECT blocker.* FROM blockers blocker WHERE blocker.id IN ({marks})"  # noqa: S608 -- controlled marks and scope
                f" AND {visible}",
                (*sorted(ids), *params),
            )
            contexts = blockers.blocker_collection_policy_contexts(rows, viewer)
            for row in rows:
                key = (entity, int(row["id"]))
                result[key] = contexts[int(row["id"])]
            continue
        if entity in _ENGAGEMENT_LINKED:
            table = _TABLES[entity][0]
            marks = ",".join("?" for _ in ids)
            visible, params = scope.visible_filter(viewer, table, "value")
            rows = db.query(
                f"SELECT value.* FROM {table} value WHERE value.id IN ({marks})"  # noqa: S608 -- closed table map and controlled marks
                f" AND {visible}",
                (*sorted(ids), *params),
            )
            contexts = engagement_linked_collection_contexts(entity, rows, viewer)
            for row in rows:
                key = (entity, int(row["id"]))
                result[key] = contexts[int(row["id"])]
            continue
        for entity_id in ids:
            context = existing_scoped(entity, entity_id, viewer)
            if context:
                result[(entity, entity_id)] = context
    return result


def filter_resource_rows(
    entity: str,
    rows: list[dict],
    viewer: scope.Viewer,
    resource_filter: Callable[[str, int, dict[str, str]], bool] | None,
) -> list[dict]:
    """Apply one projection policy callback to already-visible domain rows."""
    if resource_filter is None or not rows:
        return rows
    contexts = resource_contexts([(entity, int(row["id"])) for row in rows], viewer)
    return [
        row
        for row in rows
        if resource_filter(
            entity,
            int(row["id"]),
            contexts.get((entity, int(row["id"])), {}),
        )
    ]


def opaque_project_contexts(_viewer: scope.Viewer) -> list[tuple[int, dict[str, str]]]:
    """Return every project boundary that can affect an opaque aggregate.

    Some staffing and portfolio reports intentionally count hidden tiers and
    mask their names. Policy must therefore inspect those hidden inputs too.
    The attributes stay inside the policy engine; the response never receives
    a hidden identifier, name, or project class.
    """
    rows = db.query(
        "SELECT engagement.id, engagement.project_class, engagement.visibility"
        " FROM engagements engagement",
    )
    result = [
        (
            int(row["id"]),
            {
                "classification": str(row.get("visibility") or ""),
                "project_type": str(row.get("project_class") or ""),
            },
        )
        for row in rows
    ]
    # A standalone lesson or intake row can introduce a project class before
    # an engagement exists. Opaque reports can include both, so include all
    # of their classes as synthetic, negative resource identifiers.
    offset = 1
    for table in ("lessons", "intake_requests"):
        for row in db.query(
            f"SELECT DISTINCT value.project_class, value.visibility FROM {table} value",  # noqa: S608 -- closed table names
        ):
            result.append(
                (
                    -offset,
                    {
                        "classification": str(row.get("visibility") or ""),
                        "project_type": str(row.get("project_class") or ""),
                    },
                )
            )
            offset += 1
    return result


def has_visible_relationship_conflict(viewer: scope.Viewer) -> bool:
    """Detect a visible legacy relationship with indexed existence probes."""

    def exists(sql: str, params: tuple[object, ...]) -> bool:
        return db.query_one(sql, params) is not None

    task_visible, task_params = scope.visible_filter(viewer, "tasks", "task")
    engagement_visible, engagement_params = scope.visible_filter(
        viewer, "engagements", "engagement"
    )
    milestone_visible, milestone_params = scope.visible_filter(viewer, "milestones", "milestone")

    # Direct parent missing or hidden.
    if exists(
        "SELECT 1 AS conflict FROM tasks task LEFT JOIN engagements engagement"  # noqa: S608 -- scope emits only bound marks
        f" ON engagement.id = task.engagement_id AND {engagement_visible}"
        " WHERE task.engagement_id IS NOT NULL AND engagement.id IS NULL"
        f" AND {task_visible} LIMIT 1",
        (*engagement_params, *task_params),
    ):
        return True

    # Milestone missing or hidden.
    if exists(
        "SELECT 1 AS conflict FROM tasks task LEFT JOIN milestones milestone"  # noqa: S608 -- scope emits only bound marks
        f" ON milestone.id = task.milestone_id AND {milestone_visible}"
        " WHERE task.milestone_id IS NOT NULL AND milestone.id IS NULL"
        f" AND {task_visible} LIMIT 1",
        (*milestone_params, *task_params),
    ):
        return True

    # Milestone parent missing or hidden, and direct/milestone parents disagree.
    if exists(
        "SELECT 1 AS conflict FROM tasks task JOIN milestones milestone"  # noqa: S608 -- scope emits only bound marks
        f" ON milestone.id = task.milestone_id AND {milestone_visible}"
        " LEFT JOIN engagements engagement ON engagement.id = milestone.engagement_id"
        f" AND {engagement_visible} WHERE task.milestone_id IS NOT NULL"
        " AND ((milestone.engagement_id IS NOT NULL AND engagement.id IS NULL)"
        " OR (task.engagement_id IS NOT NULL AND milestone.engagement_id IS NOT NULL"
        " AND task.engagement_id != milestone.engagement_id))"
        f" AND {task_visible} LIMIT 1",
        (*milestone_params, *engagement_params, *task_params),
    ):
        return True

    blocker_visible, blocker_params = scope.visible_filter(viewer, "blockers", "blocker")
    linked_task_visible, linked_task_params = scope.visible_filter(viewer, "tasks", "linked_task")
    if exists(
        "SELECT 1 AS conflict FROM blockers blocker LEFT JOIN tasks linked_task"  # noqa: S608 -- scope emits only bound marks
        f" ON linked_task.id = blocker.task_id AND {linked_task_visible}"
        " WHERE blocker.task_id IS NOT NULL AND linked_task.id IS NULL"
        f" AND {blocker_visible} LIMIT 1",
        (*linked_task_params, *blocker_params),
    ):
        return True

    for entity in ("milestone", "event", "promise", "memory", "lesson", "artifact"):
        table = _TABLES[entity][0]
        value_visible, value_params = scope.visible_filter(viewer, table, "value")
        parent_visible, parent_params = scope.visible_filter(viewer, "engagements", "engagement")
        if exists(
            f"SELECT 1 AS conflict FROM {table} value LEFT JOIN engagements engagement"  # noqa: S608 -- closed table map
            f" ON engagement.id = value.engagement_id AND {parent_visible}"
            " WHERE value.engagement_id IS NOT NULL AND engagement.id IS NULL"
            f" AND {value_visible} LIMIT 1",
            (*parent_params, *value_params),
        ):
            return True
    return False


def _target_engagement_scoped(
    entity: str,
    entity_id: int,
    payload: dict,
    viewer: scope.Viewer,
) -> int:
    selected = _TABLES.get(entity)
    if selected is None or selected[1] != "engagement_id":
        return 0
    table = selected[0]
    current = 0
    if entity_id:
        visible, params = scope.visible_filter(viewer, table, "value")
        row = db.query_one(
            f"SELECT value.engagement_id FROM {table} value"  # noqa: S608 -- closed table map and scope marks
            f" WHERE value.id = ? AND {visible}",
            (entity_id, *params),
        )
        current = _integer((row or {}).get("engagement_id"))
    if "engagement_id" in payload:
        requested = _integer(payload.get("engagement_id"))
        if requested:
            current = max(requested, 0)
    if entity == "milestone" and not entity_id and not current:
        name = str(payload.get("project") or "")
        if name:
            visible, params = scope.visible_filter(viewer, "engagements", "engagement")
            row = db.query_one(
                f"SELECT engagement.id FROM engagements engagement"  # noqa: S608 -- scope emits bound marks
                f" WHERE engagement.name = ? AND {visible}",
                (name, *params),
            )
            current = _integer((row or {}).get("id"))
    return current


def for_route_scoped(
    resource_type: str,
    resource_id: str,
    payload: dict,
    viewer: scope.Viewer,
) -> dict[str, str]:
    """Return REST policy data without reading a hidden resource or parent."""
    if resource_type == "playbooks":
        return for_route(resource_type, resource_id, payload)
    entity = _ROUTE_ENTITIES.get(resource_type)
    if entity is None:
        return {}
    try:
        entity_id = int(resource_id or 0)
    except ValueError:
        entity_id = 0
    current = existing_scoped(entity, entity_id, viewer) if entity_id else {}
    current_relationship_conflict = bool(current.get("relationship_conflict"))
    classification = str(current.get("classification") or "")
    project_type = str(current.get("project_type") or "")
    selected = _TABLES[entity]
    if entity == "allocation":
        return current
    if not entity_id:
        classification = str(payload.get("visibility") or scope.WORKSPACE)
    if not entity_id and selected[1] == "project_class":
        project_type = str(payload.get("project_class") or "")
    elif selected[1] == "engagement_id":
        target = _target_engagement_scoped(entity, entity_id, payload, viewer)
        project_type = (
            str(payload.get("project_class") or "general")
            if entity == "lesson" and not target
            else _visible_engagement_project_type(target, viewer)
        )
    result = dict(current)
    result.update({"classification": classification, "project_type": project_type})
    if current_relationship_conflict:
        result["current_relationship_conflict"] = "true"
    if selected[1] == "engagement_id" and target and not project_type:
        result["relationship_conflict"] = "true"
    return result


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
