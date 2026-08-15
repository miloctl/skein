"""Milestone and task services — the single write path for both REST and tools."""

import re
from collections.abc import Callable

from .. import db
from . import scope
from .search import index_record

MILESTONE_STATUSES = ("planned", "in_progress", "blocked", "done")
TASK_STATUSES = ("todo", "in_progress", "blocked", "done")
PRIORITIES = ("low", "medium", "high", "urgent")
WEEK_RE = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")

# Bounds for the two free-text fields, enforced HERE because this is the only
# write path. routes/api.py imports these for its own Field(max_length=...) so
# the two doors cannot drift: the REST models capped these and the service did
# not, so an agent or MCP caller wrote a title the PATCH route then refused —
# a row the system wrote that its own UI could not edit.
TITLE_LEN = 200
DESCRIPTION_LEN = 4000


def _event_actor_kind(origin: str) -> str:
    if origin.startswith("agent"):
        return "agent"
    if origin == "human":
        return "human"
    return "service"


def _emit_task_event(
    event_type: str,
    task_id: int,
    *,
    actor: str,
    origin: str,
    visibility: str,
    changes: tuple[str, ...],
    correlation_id: str,
    actor_kind: str,
) -> None:
    """Emit from the shared write path so every caller gets one event."""
    from ..public.events import EventActor, ResourceReference, _emit_event

    _emit_event(
        event_type,
        actor=EventActor(name=actor, kind=actor_kind or _event_actor_kind(origin)),
        origin=origin,
        resource=ResourceReference(type="task", id=str(task_id)),
        changes=changes,
        correlation_id=correlation_id,
        visibility=visibility,
    )


def _bounded(entity: str, title: str, description: str) -> None:
    """Names the entity, like every refusal beside it ("task title is
    required"). A caller writing a milestone and a task in one turn otherwise
    reads a refusal that does not say which write failed."""
    if len(title) > TITLE_LEN:
        raise ValueError(f"{entity} title must be {TITLE_LEN} characters or fewer")
    if len(description) > DESCRIPTION_LEN:
        raise ValueError(
            f"{entity} description must be {DESCRIPTION_LEN} characters or fewer."
            " Shorten it, or save the long text as a note."
        )


def create_milestone(
    title: str,
    description: str = "",
    project: str = "default",
    owner: str = "",
    due_date: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    if not title.strip():
        raise ValueError("milestone title is required")
    _bounded("milestone", title, description)
    db.validate_date("due_date", due_date, allow_clear=False)
    ts = db.now()
    # the membership check belongs INSIDE the insert's transaction — bare, it
    # opens its own connection, so a person removed from the crew between the
    # check and the write still scopes the row (services/scope.py::resolve_write)
    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor)
        visible, params = scope.visible_filter(
            scope.Viewer.for_actor(actor), "engagements", "engagement"
        )
        eng = db.query_one(
            f"SELECT engagement.* FROM engagements engagement"  # noqa: S608 -- scope emits bound marks
            f" WHERE engagement.name = ? AND {visible}",
            (project, *params),
        )
        if eng is not None:
            scope.assert_relationship_contains(
                eng["visibility"],
                eng["crew_id"],
                tier,
                crew,
                child_label="milestone",
            )
        mid = db.execute(
            "INSERT INTO milestones (project, engagement_id, title, description, owner,"
            " due_date, origin, created_by, created_at, updated_at, visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (
                project,
                eng["id"] if eng else None,
                title,
                description,
                owner,
                due_date or None,
                origin,
                actor,
                ts,
                ts,
                tier,
                crew,
            ),
        )
        # "team" is every person on the roster and the message quotes the
        # title, so a scoped milestone tells nobody — its own list still shows
        # the unlinked project to the people who can read it
        if eng is None and project != "default" and tier == scope.WORKSPACE:
            from .notifications import notify

            notify(
                "team",
                lambda source: (
                    f"Milestone #{source['id']} '{source['title']}' names project"
                    f" '{source['project']}' but no engagement matches — it will not"
                    " count in health/forecast until you relink it."
                ),
                tier="digest",
                link="/dashboard",
                source_entity="milestone",
                source_id=mid,
            )
        db.log_activity(actor, "create_milestone", scope.detail(tier, f"#{mid}", title))
        index_record("milestone", mid, title, f"{description} {project} {owner}")
    return {"id": mid, "title": title, "status": "planned"}


def update_milestone(
    milestone_id: int,
    status: str = "",
    title: str = "",
    description: str = "",
    owner: str = "",
    due_date: str = "",
    engagement_id: int = 0,
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    if status and status not in MILESTONE_STATUSES:
        raise ValueError(f"status must be one of {MILESTONE_STATUSES}")
    _bounded("milestone", title, description)
    db.validate_date("due_date", due_date)
    with db.transaction():
        current = db.query_one("SELECT * FROM milestones WHERE id = ?", (milestone_id,))
        if not current:
            raise scope.missing("milestones", milestone_id)
        scope.assert_editable("milestones", current, actor, verb="update")
        fields: dict[str, str | int | None] = {
            k: v
            for k, v in [
                ("status", status),
                ("title", title),
                ("description", description),
                ("owner", owner),
                ("due_date", due_date),
            ]
            if v
        }
        target_id = max(engagement_id, 0) if engagement_id else int(current["engagement_id"] or 0)
        if target_id:
            try:
                target = _visible_link("engagements", target_id, actor)
            except ValueError as exc:
                # This is a relationship id from the body or current row, not
                # the path milestone. Keep the established 400 contract while
                # giving hidden and absent targets the same refusal.
                raise ValueError(scope.missing_text("engagements", target_id)) from exc
            scope.assert_relationship_contains(
                target["visibility"],
                target["crew_id"],
                current["visibility"],
                current["crew_id"],
                child_label="milestone",
            )
            linked_tasks = db.query(
                "SELECT engagement_id, visibility, crew_id FROM tasks WHERE milestone_id = ?",
                (milestone_id,),
            )
            for task in linked_tasks:
                direct = int(task["engagement_id"] or 0)
                if direct and direct != target_id:
                    raise ValueError(
                        "a task's milestone and engagement must belong to the same engagement"
                    )
                scope.assert_relationship_contains(
                    target["visibility"],
                    target["crew_id"],
                    task["visibility"],
                    task["crew_id"],
                )
        if engagement_id:
            fields["engagement_id"] = None if engagement_id < 0 else engagement_id
        if not fields:
            raise ValueError("nothing to update")
        for clearable, empty in (("due_date", None), ("owner", ""), ("description", "")):
            if fields.get(clearable) == "-":
                fields[clearable] = empty
        if status == "done" and current["status"] != "done":
            fields["completed_at"] = db.now()
        elif status and status != "done" and current["status"] == "done":
            fields["completed_at"] = None
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE milestones SET {sets}, updated_at = ? WHERE id = ?",  # noqa: S608 -- keys are fixed above
            (*fields.values(), db.now(), milestone_id),
        )
        db.log_activity(actor, "update_milestone", f"#{milestone_id} {status or 'edited'}")
        row = db.query_one("SELECT * FROM milestones WHERE id = ?", (milestone_id,))
        if row:
            index_record(
                "milestone",
                milestone_id,
                row["title"],
                f"{row['description']} {row['project']} {row['owner']}",
            )
    return {"id": milestone_id, "updated": list(fields)}


def list_milestones(
    project: str = "", status: str = "", viewer: scope.Viewer = scope.NOBODY
) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "milestones", "milestone")
    engagement_visible, engagement_params = scope.visible_filter(
        viewer, "engagements", "engagement"
    )
    sql, params = (
        f"SELECT milestone.*, engagement.id AS visible_engagement_id"  # noqa: S608 -- scope emits bound marks
        " FROM milestones milestone LEFT JOIN engagements engagement"
        " ON engagement.id = milestone.engagement_id"
        f" AND {engagement_visible} WHERE {frag}",
        [*engagement_params, *vp],
    )
    if project:
        sql += " AND milestone.project = ?"
        params.append(project)
    if status:
        sql += " AND milestone.status = ?"
        params.append(status)
    rows = db.query(
        sql + " ORDER BY milestone.due_date IS NULL, milestone.due_date, milestone.id LIMIT 500",
        tuple(params),
    )
    for row in rows:
        visible_engagement = row.pop("visible_engagement_id", None)
        if row.get("engagement_id") and visible_engagement is None:
            row["engagement_id"] = None
    return rows


def milestone_collection_policy_contexts(
    rows: list[dict], viewer: scope.Viewer
) -> dict[int, dict[str, str]]:
    """Resolve project policy for milestones that already passed row visibility."""
    milestone_ids = sorted({int(row["id"]) for row in rows})
    if not milestone_ids:
        return {}
    marks = ",".join("?" for _ in milestone_ids)
    raw_links = {
        int(row["id"]): int(row.get("engagement_id") or 0)
        for row in db.query(
            f"SELECT id, engagement_id FROM milestones WHERE id IN ({marks})",  # noqa: S608 -- marks are controlled
            tuple(milestone_ids),
        )
    }
    engagement_ids = sorted(set(raw_links.values()) - {0})
    projects: dict[int, str] = {}
    if engagement_ids:
        engagement_marks = ",".join("?" for _ in engagement_ids)
        visible, params = scope.visible_filter(viewer, "engagements", "engagement")
        projects = {
            int(row["id"]): str(row.get("project_class") or "")
            for row in db.query(
                f"SELECT engagement.id, engagement.project_class FROM engagements engagement"  # noqa: S608 -- marks and scope are controlled
                f" WHERE engagement.id IN ({engagement_marks}) AND {visible}",
                (*engagement_ids, *params),
            )
        }
    result: dict[int, dict[str, str]] = {}
    for row in rows:
        milestone_id = int(row["id"])
        engagement_id = raw_links.get(milestone_id, 0)
        attributes = {
            "classification": str(row.get("visibility") or ""),
            "project_type": projects.get(engagement_id, ""),
        }
        if engagement_id and engagement_id not in projects:
            attributes["relationship_conflict"] = "true"
        result[milestone_id] = attributes
    return result


def create_task(
    title: str,
    description: str = "",
    milestone_id: int = 0,
    assignee: str = "",
    priority: str = "medium",
    due_date: str = "",
    engagement_id: int = 0,
    *,
    actor: str = "system",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
    correlation_id: str = "",
    event_actor_kind: str = "",
) -> dict:
    """Create a task and validate all linked audiences in one transaction."""
    with db.transaction():
        return _create_task_locked(
            title,
            description,
            milestone_id,
            assignee,
            priority,
            due_date,
            engagement_id,
            actor=actor,
            origin=origin,
            visibility=visibility,
            crew_id=crew_id,
            correlation_id=correlation_id,
            event_actor_kind=event_actor_kind,
        )


def _create_task_locked(
    title: str,
    description: str = "",
    milestone_id: int = 0,
    assignee: str = "",
    priority: str = "medium",
    due_date: str = "",
    engagement_id: int = 0,
    *,
    actor: str = "system",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
    correlation_id: str = "",
    event_actor_kind: str = "",
) -> dict:
    if not title.strip():
        raise ValueError("task title is required")
    _bounded("task", title, description)
    db.validate_date("due_date", due_date, allow_clear=False)
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    ts = db.now()
    tier, cid = scope.resolve_write(visibility, crew_id, actor=actor)
    _assert_task_relationships(
        milestone_id,
        engagement_id,
        actor=actor,
        task_visibility=tier,
        task_crew_id=cid,
    )
    with db.transaction():
        scope.assert_readable_by(tier, cid, assignee, label="assignee", author=actor)
        tid = db.execute(
            "INSERT INTO tasks (milestone_id, engagement_id, title, description, assignee,"
            " priority, due_date, origin, created_by, created_at, updated_at,"
            " visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (
                milestone_id or None,
                engagement_id or None,
                title,
                description,
                assignee,
                priority,
                due_date or None,
                origin,
                actor,
                ts,
                ts,
                tier,
                cid,
            ),
        )
        db.log_activity(actor, "create_task", scope.detail(tier, f"#{tid}", title))
        index_record("task", tid, title, f"{description} {assignee}")
        _emit_task_event(
            "skein.task.created",
            tid,
            actor=actor,
            origin=origin,
            visibility=tier,
            changes=(
                "title",
                "description",
                "milestone_id",
                "engagement_id",
                "assignee",
                "priority",
                "due_date",
                "visibility",
                "crew_id",
            ),
            correlation_id=correlation_id,
            actor_kind=event_actor_kind,
        )
    from .mentions import scan

    # title too: a short `todo: ask @mira ...` capture lands entirely in the
    # title, and a mention there must ping like one in the description
    scan("task", tid, f"{title} {description}", actor=actor, link="/dashboard")
    return {"id": tid, "title": title, "status": "todo"}


def _visible_link(table: str, row_id: int, actor: str) -> dict:
    """Resolve scope metadata only after the actor-visible id probe passes."""
    viewer = scope.Viewer.for_actor(actor)
    visible, params = scope.visible_filter(viewer, table)
    if not db.query_one(
        f"SELECT id FROM {table} WHERE id = ? AND {visible}",  # noqa: S608 -- table is a private constant
        (row_id, *params),
    ):
        raise ValueError(scope.missing_text(table, row_id))
    row = db.query_one(
        f"SELECT * FROM {table} WHERE id = ?",  # noqa: S608 -- table is a private constant
        (row_id,),
    )
    if row is None:  # The caller holds the surrounding write transaction.
        raise ValueError(scope.missing_text(table, row_id))
    return row


def _assert_task_relationships(
    milestone_id: int,
    engagement_id: int,
    *,
    actor: str,
    task_visibility: str,
    task_crew_id: int | None,
) -> dict[str, str]:
    """Validate link visibility without turning hidden ids into an oracle."""
    project_type = ""
    direct_engagement = None
    if engagement_id:
        engagement = _visible_link("engagements", engagement_id, actor)
        direct_engagement = engagement
        scope.assert_relationship_contains(
            engagement["visibility"],
            engagement["crew_id"],
            task_visibility,
            task_crew_id,
        )
        project_type = str(engagement.get("project_class") or "")
    if not milestone_id:
        return {"classification": task_visibility, "project_type": project_type}
    milestone = _visible_link("milestones", milestone_id, actor)
    scope.assert_relationship_contains(
        milestone["visibility"],
        milestone["crew_id"],
        task_visibility,
        task_crew_id,
    )
    parent = db.query_one("SELECT engagement_id FROM milestones WHERE id = ?", (milestone_id,))
    parent_id = int((parent or {}).get("engagement_id") or 0)
    if not parent_id:
        return {"classification": task_visibility, "project_type": project_type}
    try:
        engagement = _visible_link("engagements", parent_id, actor)
    except ValueError as exc:
        # Do not reveal that a visible milestone points to a hidden parent.
        raise ValueError(scope.missing_text("milestones", milestone_id)) from exc
    if direct_engagement is not None and int(direct_engagement["id"]) != parent_id:
        raise ValueError("a task's milestone and engagement must belong to the same engagement")
    scope.assert_relationship_contains(
        engagement["visibility"],
        engagement["crew_id"],
        task_visibility,
        task_crew_id,
    )
    if not engagement_id:
        project_type = str(engagement.get("project_class") or "")
    return {"classification": task_visibility, "project_type": project_type}


def task_create_policy_context(
    *,
    milestone_id: int = 0,
    engagement_id: int = 0,
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
    actor: str,
) -> dict[str, str]:
    """Resolve the actor-visible target state used by task create policy."""
    with db.transaction():
        tier, resolved_crew = scope.resolve_write(visibility, crew_id, actor=actor)
        return _assert_task_relationships(
            milestone_id,
            engagement_id,
            actor=actor,
            task_visibility=tier,
            task_crew_id=resolved_crew,
        )


def task_update_policy_context(
    task_id: int,
    payload: dict,
    *,
    actor: str,
) -> dict[str, str]:
    """Resolve one editable task and its proposed links for policy evaluation."""
    with db.transaction():
        current = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if current is None:
            raise scope.missing("tasks", task_id)
        scope.assert_editable("tasks", current, actor, verb="update")
        milestone_id = int(current["milestone_id"] or 0)
        engagement_id = int(current["engagement_id"] or 0)
        if int(payload.get("milestone_id") or 0):
            milestone_id = max(int(payload["milestone_id"]), 0)
        if int(payload.get("engagement_id") or 0):
            engagement_id = max(int(payload["engagement_id"]), 0)
        return _assert_task_relationships(
            milestone_id,
            engagement_id,
            actor=actor,
            task_visibility=str(current["visibility"]),
            task_crew_id=current["crew_id"],
        )


def task_read_policy_context(task: dict, viewer: scope.Viewer) -> dict[str, str]:
    """Return context from raw links while keeping hidden parent data concealed."""
    task_id = int(task.get("id") or 0)
    if not task_id:
        return {"classification": str(task.get("visibility") or ""), "project_type": ""}
    return task_collection_policy_contexts([task], viewer)[task_id]


def task_collection_policy_contexts(
    tasks: list[dict], viewer: scope.Viewer
) -> dict[int, dict[str, str]]:
    """Resolve policy metadata without trusting already-redacted relationships."""
    task_ids = sorted({int(task["id"]) for task in tasks})
    if not task_ids:
        return {}
    task_marks = ",".join("?" for _ in task_ids)
    raw_rows = db.query(
        f"SELECT id, engagement_id, milestone_id FROM tasks WHERE id IN ({task_marks})",  # noqa: S608 -- marks are controlled
        tuple(task_ids),
    )
    raw_links = {
        int(row["id"]): (
            int(row.get("engagement_id") or 0),
            int(row.get("milestone_id") or 0),
        )
        for row in raw_rows
    }
    engagement_ids = sorted({link[0] for link in raw_links.values()} - {0})
    milestone_ids = sorted({link[1] for link in raw_links.values()} - {0})
    engagements: dict[int, str] = {}
    if engagement_ids:
        visible, params = scope.visible_filter(viewer, "engagements", "engagement")
        marks = ",".join("?" for _ in engagement_ids)
        rows = db.query(
            f"SELECT engagement.id, engagement.project_class FROM engagements engagement"  # noqa: S608 -- marks and scope are controlled
            f" WHERE engagement.id IN ({marks}) AND {visible}",
            (*engagement_ids, *params),
        )
        engagements = {int(row["id"]): str(row.get("project_class") or "") for row in rows}
    milestones: dict[int, tuple[int, int, str]] = {}
    if milestone_ids:
        milestone_visible, milestone_params = scope.visible_filter(
            viewer, "milestones", "milestone"
        )
        engagement_visible, engagement_params = scope.visible_filter(
            viewer, "engagements", "engagement"
        )
        marks = ",".join("?" for _ in milestone_ids)
        rows = db.query(
            f"SELECT milestone.id, milestone.engagement_id,"  # noqa: S608 -- marks and scope are controlled
            " engagement.id AS visible_engagement_id,"
            " engagement.project_class FROM milestones milestone"
            " LEFT JOIN engagements engagement ON engagement.id = milestone.engagement_id"
            f" AND {engagement_visible} WHERE milestone.id IN ({marks})"
            f" AND {milestone_visible}",
            (*engagement_params, *milestone_ids, *milestone_params),
        )
        milestones = {
            int(row["id"]): (
                int(row.get("engagement_id") or 0),
                int(row.get("visible_engagement_id") or 0),
                str(row.get("project_class") or ""),
            )
            for row in rows
        }
    result: dict[int, dict[str, str]] = {}
    for task in tasks:
        task_id = int(task["id"])
        engagement_id, milestone_id = raw_links.get(task_id, (0, 0))
        milestone_engagement, visible_milestone_engagement, milestone_project = milestones.get(
            milestone_id, (0, 0, "")
        )
        attributes = {
            "classification": str(task.get("visibility") or ""),
            "project_type": engagements.get(engagement_id, "") or milestone_project,
        }
        hidden_relationship = bool(
            (engagement_id and engagement_id not in engagements)
            or (milestone_id and milestone_id not in milestones)
            or (milestone_engagement and not visible_milestone_engagement)
        )
        if hidden_relationship or (
            engagement_id and milestone_engagement and engagement_id != milestone_engagement
        ):
            attributes["project_type"] = ""
            attributes["relationship_conflict"] = "true"
        result[task_id] = attributes
    return result


def consistent_task_rows(tasks: list[dict], viewer: scope.Viewer) -> list[dict]:
    """Remove visible legacy tasks whose project relationship is unsafe."""
    contexts = task_collection_policy_contexts(tasks, viewer)
    return [
        task for task in tasks if not contexts.get(int(task["id"]), {}).get("relationship_conflict")
    ]


# portfolio._WAIT_SATISFIED keys mirror this tuple — a new type needs its
# satisfied-query there or /portfolio KeyErrors on the first wait using it
WAITING_ON_TYPES = ("task", "blocker", "promise")
_WAITING_TABLES = {"task": "tasks", "blocker": "blockers", "promise": "promises"}


def update_task(
    task_id: int,
    status: str = "",
    assignee: str = "",
    priority: str = "",
    due_date: str = "",
    description: str = "",
    title: str = "",
    committed_week: str = "",
    waiting_on: str = "",
    milestone_id: int = 0,
    engagement_id: int = 0,
    # keyword-only and absent from the REST patch model on purpose: the forge
    # webhook writes this link, a person never retypes it
    forge_url: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
    # suffix for the activity detail. A machine write must be able to say
    # which channel it came through, or its row reads as a person's own edit
    # in a ledger that can never be corrected.
    note: str = "",
    # whether the caller proved who they are (a personal key, a validated
    # sign-in). Only _settle_acceptance reads it, and only to record the
    # strength of the verdict a direct close stands in for. Defaults False so
    # every machine path records the weaker, truthful thing.
    strong: bool = False,
    correlation_id: str = "",
    event_actor_kind: str = "",
) -> dict:
    """Update a task under one transaction-bound relationship snapshot."""
    with db.transaction():
        return _update_task_locked(
            task_id,
            status,
            assignee,
            priority,
            due_date,
            description,
            title,
            committed_week,
            waiting_on,
            milestone_id,
            engagement_id,
            forge_url,
            actor=actor,
            origin=origin,
            note=note,
            strong=strong,
            correlation_id=correlation_id,
            event_actor_kind=event_actor_kind,
        )


def _update_task_locked(
    task_id: int,
    status: str = "",
    assignee: str = "",
    priority: str = "",
    due_date: str = "",
    description: str = "",
    title: str = "",
    committed_week: str = "",
    waiting_on: str = "",
    milestone_id: int = 0,
    engagement_id: int = 0,
    forge_url: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
    note: str = "",
    strong: bool = False,
    correlation_id: str = "",
    event_actor_kind: str = "",
) -> dict:
    if status and status not in TASK_STATUSES:
        raise ValueError(f"status must be one of {TASK_STATUSES}")
    _bounded("task", title, description)
    db.validate_date("due_date", due_date)
    if priority and priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    if committed_week and committed_week != "-" and not WEEK_RE.match(committed_week):
        raise ValueError("committed_week must look like 2026-W31 (or '-' to clear)")
    # waiting_on: "blocker:12" (what is this stuck behind — deliberately NOT
    # Gantt), or "-" to clear
    waiting_type: str | None = None
    waiting_id: int | None = None
    if waiting_on and waiting_on != "-":
        kind, _, ref = waiting_on.partition(":")
        # isdecimal, not isdigit: '²' passes isdigit but blows up int()
        if kind not in WAITING_ON_TYPES or not ref.strip().lstrip("#").isdecimal():
            raise ValueError(
                f"waiting_on must look like 'task:12', 'blocker:3', or"
                f" 'promise:7' (one of {WAITING_ON_TYPES}), or '-' to clear"
            )
        waiting_type, waiting_id = kind, int(ref.strip().lstrip("#"))
        if kind == "task" and waiting_id == task_id:
            raise ValueError("a task cannot wait on itself")
    current = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not current:
        raise scope.missing("tasks", task_id)
    scope.assert_editable("tasks", current, actor, verb="update")
    if waiting_type and waiting_id:
        waiting_row = _visible_link(_WAITING_TABLES[waiting_type], waiting_id, actor)
        scope.assert_relationship_contains(
            waiting_row["visibility"],
            waiting_row["crew_id"],
            current["visibility"],
            current["crew_id"],
        )
    # delegated work is closed by the sponsor's verdict, never by an agent
    # marking it done — otherwise submit_for_acceptance is a paper wall.
    #
    # No agent_verified exemption: approve_change applies EVERY proposal with
    # exactly that origin, so exempting it made the guard void on the review
    # path. An agent could file a generic `task` update proposal instead of
    # submit_for_acceptance, and any human — not the sponsor — could approve
    # it, with no reason on record and no override marking. accept_completion
    # writes its own UPDATE and never routes through here, so the sponsor's
    # real verdict is unaffected.
    if status == "done" and current["delegated_agent"]:
        from .users import is_agent

        if is_agent(actor):
            # TerminalReject, not ValueError: an agent's own delegated-done
            # proposal can never be approved into success, so approve_change
            # must settle it rejected rather than reset it to pending, where
            # it would clutter /review until a human rejects it by hand
            raise db.TerminalReject(
                f"task #{task_id} is delegated — submit_for_acceptance gets the"
                " sponsor's verdict; only that closes it"
            )
        # ...and no OTHER human either. The agent half of this guard was
        # complete and the human half did not exist, so any teammate who could
        # reach PATCH /api/tasks/{id} closed delegated work with one field —
        # no sponsor verdict, no reason on record, no override marking, and no
        # trust signal for the agent that did the work. review._sponsor_override
        # is the path for acting when the sponsor cannot: it takes a reason and
        # marks the verdict so it never feeds a streak.
        #
        # The sponsor themselves is allowed through: the verdict is theirs on
        # either path, and refusing them here would make the acceptance
        # proposal the only way to close work they already own.
        _assert_sponsor(task_id, current, actor)
    # Reassigning a delegated task away from its agent CLEARS the delegation
    # (below), so it is the same transition wearing a different field. Guarded
    # here or the refusal above is two PATCH calls deep: reassign to clear
    # `delegated_agent` and `sponsor`, then close the now-undelegated task.
    # That path also strands the acceptance proposal pending forever — its
    # apply raises "already done", which resets it to pending on every verdict.
    if assignee and current["delegated_agent"] and assignee != current["delegated_agent"]:
        _assert_sponsor(task_id, current, actor, verb="end this delegation")
    fields: dict[str, str | int | None] = {
        k: v
        for k, v in [
            ("status", status),
            ("assignee", assignee),
            ("priority", priority),
            ("due_date", due_date),
            ("description", description),
            ("title", title),
            ("committed_week", committed_week),
            ("forge_url", forge_url),
        ]
        if v
    }
    if waiting_on == "-":
        fields["waiting_on_type"] = None
        fields["waiting_on_id"] = None
    elif waiting_type:
        fields["waiting_on_type"] = waiting_type
        fields["waiting_on_id"] = waiting_id
    for link_field, link_id in (
        ("milestone_id", milestone_id),
        ("engagement_id", engagement_id),
    ):
        if link_id:
            fields[link_field] = None if link_id < 0 else link_id
    final_milestone = int(fields.get("milestone_id", current["milestone_id"]) or 0)
    final_engagement = int(fields.get("engagement_id", current["engagement_id"]) or 0)
    _assert_task_relationships(
        final_milestone,
        final_engagement,
        actor=actor,
        task_visibility=current["visibility"],
        task_crew_id=current["crew_id"],
    )
    if not fields:
        raise ValueError("nothing to update")
    if committed_week == "-":
        fields["committed_week"] = None
    # "-" clears any clearable field — the single write path must be able to
    # unset a wrong due date without hand-editing the database
    for clearable, empty in (("due_date", None), ("assignee", ""), ("description", "")):
        if fields.get(clearable) == "-":
            fields[clearable] = empty
    if status == "done" and current["status"] != "done":
        fields["completed_at"] = db.now()  # flow metrics read this, not updated_at
    elif status and status != "done" and current["status"] == "done":
        fields["completed_at"] = None
    # the sentinel is resolved BEFORE the two tests below, not only into
    # `fields`. Testing the raw parameter sent the literal "-" to
    # assert_readable_by, which asked whether a person named "-" is in the
    # crew — so un-assigning a crew or private task was refused outright, and
    # the only write path could not undo an assignment it had made.
    if assignee == "-":
        assignee = ""
    if assignee:
        # the same check create_task makes: a reassignment reaches a name the
        # original write never saw, and an assignee who cannot read the task
        # is given work that does not exist for them
        scope.assert_readable_by(
            current["visibility"],
            current["crew_id"],
            assignee,
            label="assignee",
            author=current["created_by"],
        )
    # reassigning a delegated task away from its agent ends the delegation —
    # otherwise both parties see it as theirs
    if assignee and current["delegated_agent"] and assignee != current["delegated_agent"]:
        fields["delegated_agent"] = ""
        fields["sponsor"] = ""
    sets = ", ".join(f"{k} = ?" for k in fields)
    with db.transaction():
        db.execute(
            f"UPDATE tasks SET {sets}, updated_at = ? WHERE id = ?",  # noqa: S608 — keys hardcoded
            (*fields.values(), db.now(), task_id),
        )
        db.log_activity(actor, "update_task", f"#{task_id} {status or 'edited'}{note}")
        # The sponsor just answered the acceptance ask by hand, so the proposal
        # waiting for that answer has to be settled here or it never can be.
        if fields.get("status") == "done" and current["delegated_agent"]:
            _settle_acceptance(task_id, actor, strong, current["delegated_agent"])
        row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if row:
            index_record("task", task_id, row["title"], f"{row['description']} {row['assignee']}")
            if fields.get("description") or fields.get("title"):
                from .mentions import scan

                scan(
                    "task",
                    task_id,
                    f"{row['title']} {row['description']}",
                    actor=actor,
                    link="/dashboard",
                )
        _emit_task_event(
            "skein.task.updated",
            task_id,
            actor=actor,
            origin=origin,
            visibility=current["visibility"],
            changes=tuple(fields),
            correlation_id=correlation_id,
            actor_kind=event_actor_kind,
        )
    return {"id": task_id, "updated": list(fields)}


def _assert_sponsor(task_id: int, task: dict, actor: str, verb: str = "close it") -> None:
    """Only the sponsor may end a delegation, however the write is spelled.

    Two writes end one: setting status to done, and reassigning the task away
    from its agent (which clears `delegated_agent` and `sponsor`). Guarding
    only the first leaves the second as a two-call bypass of the whole
    acceptance loop — no verdict, no reason on record, no override marking, no
    trust signal for the agent that did the work, and an acceptance proposal
    stranded pending because its apply now raises "already done".

    An unsponsored delegation is not refused: nobody holds it, so nobody's
    verdict is being taken. `review._sponsor_override` covers acting for a
    sponsor who cannot, and takes a reason for the record.
    """
    if task["sponsor"] and actor != task["sponsor"]:
        # TerminalReject for an AGENT proposer, the same reason the
        # delegated-done guard above uses one: approve_change applies with
        # `actor = change["proposed_by"]`, so an agent-filed reassignment can
        # never be approved into success — and a plain PermissionError lands in
        # the generic handler, which resets the proposal to pending and
        # boomerangs it on every future verdict.
        from .users import is_agent

        if is_agent(actor):
            raise db.TerminalReject(
                f"task #{task_id} is sponsored by {task['sponsor']} — only the sponsor can {verb}"
            )
        raise PermissionError(
            f"task #{task_id} is sponsored by {task['sponsor']} — only the sponsor"
            f" can {verb}. Judge the acceptance proposal in Approvals to act for"
            " them, which puts the reason on record"
        )


def _settle_acceptance(task_id: int, actor: str, strong: bool, agent: str) -> None:
    """Close the acceptance proposal the sponsor has just answered by hand.

    APPROVED, not rejected. The proposal asks one question — does the sponsor
    accept this work — and closing the task is a yes. A rejection here would
    be a false record of the verdict AND would feed the agent's demotion
    streak (services/delegation.py::trust_scores counts consecutive strong
    non-override rejections), punishing it for work that was accepted.

    `result_id` is the task, so provenance.lineage still finds the chain from
    the row back to the proposal that produced it.

    Only rows still pending are touched: a proposal already judged carries a
    real verdict, and overwriting it would erase who made the call.

    `strong` records what actually happened. Hardcoded to 0, provenance.lineage
    reports `verdict_is_weak` for a verdict a sponsor made with a personal key,
    and the panel then prints "Nobody used a personal API key for that verdict"
    about a deployment where somebody did.

    Every row is claimed in ONE statement and logged only if that statement
    claimed it. `waiting` is read first, but a concurrent reject in another tab
    can settle a row between the read and the UPDATE — and a ledger row saying
    a rejected proposal was approved can never be corrected, because `activity`
    rows carrying a `seq` are hash-chained (CLAUDE.md).
    """
    # one transaction with the caller's task UPDATE is not possible from here
    # (update_task writes before this runs), so each row is claimed on its own
    # `status = 'pending'` test and only a winning claim is logged
    waiting = db.query(
        "SELECT id FROM pending_changes WHERE entity = 'task_completion'"
        " AND entity_id = ? AND status = 'pending'",
        (task_id,),
    )
    if not waiting:
        return
    from .delegation import clear_acceptance_ping

    for row in waiting:
        claimed = db.execute_rowcount(
            "UPDATE pending_changes SET status = 'approved', reviewed_by = ?, reviewed_at = ?,"
            " reviewed_strong = ?, reviewed_override = 0, result_id = ?,"
            " review_note = 'the sponsor closed the task directly'"
            " WHERE id = ? AND status = 'pending'",
            (actor, db.now(), int(strong), task_id, row["id"]),
        )
        if not claimed:
            continue  # somebody else judged it first, and their verdict stands
        db.log_activity(
            actor, "approve_change", f"#{row['id']} -> task #{task_id} (closed directly)"
        )
    clear_acceptance_ping(task_id, agent)


def get_task(task_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """One task with its milestone and engagement names, for the side peek.

    Raises scope.missing for an unreadable row exactly as for an absent one:
    task ids are sequential integers, so any other pairing answers "does #12
    exist" for every id a caller cares to walk (services/scope.py::Viewer).

    The two joined titles take their OWN filters on the nullable side of the
    join, the way list_tasks_joined records: in WHERE they would drop every
    task with no milestone and turn the join INNER, and unfiltered they serve
    a private milestone's title beside a workspace task."""
    frag, vp = scope.visible_filter(viewer, "tasks", alias="t")
    mfrag, mp = scope.visible_filter(viewer, "milestones", alias="m")
    efrag, ep = scope.visible_filter(viewer, "engagements", alias="e")
    wtfrag, wtp = scope.visible_filter(viewer, "tasks", alias="waiting_task")
    wbfrag, wbp = scope.visible_filter(viewer, "blockers", alias="waiting_blocker")
    wpfrag, wpp = scope.visible_filter(viewer, "promises", alias="waiting_promise")
    row = db.query_one(
        "SELECT t.*, m.id AS visible_milestone_id, m.title AS milestone_title,"  # noqa: S608 — scope.visible_filter emits only bound marks
        " e.id AS visible_engagement_id, e.name AS engagement_name,"
        " COALESCE(waiting_task.id, waiting_blocker.id, waiting_promise.id)"
        " AS visible_waiting_id"
        f" FROM tasks t LEFT JOIN milestones m ON m.id = t.milestone_id AND {mfrag}"
        f" LEFT JOIN engagements e ON e.id = t.engagement_id AND {efrag}"
        " LEFT JOIN tasks waiting_task ON t.waiting_on_type = 'task'"
        f" AND waiting_task.id = t.waiting_on_id AND {wtfrag}"
        " LEFT JOIN blockers waiting_blocker ON t.waiting_on_type = 'blocker'"
        f" AND waiting_blocker.id = t.waiting_on_id AND {wbfrag}"
        " LEFT JOIN promises waiting_promise ON t.waiting_on_type = 'promise'"
        f" AND waiting_promise.id = t.waiting_on_id AND {wpfrag}"
        f" WHERE t.id = ? AND {frag}",
        (*mp, *ep, *wtp, *wbp, *wpp, task_id, *vp),
    )
    if not row:
        raise scope.missing("tasks", task_id)
    # what finishing it releases, resolved here so the peek and any other
    # reader of one task get the same answer
    task = _redact_hidden_task_links(row)
    return {
        **task,
        **downstream(task_id, viewer),
        "blockers": blocking(task_id, viewer),
        # the finding that ASKED for this work, if one did. `source_finding_id`
        # was stamped at conversion (services/insights.py) and nothing read it
        # back, so a task existed because a rule fired and the task could not
        # say so — which is the half of the loop that tells a reader whether
        # the rule was worth keeping.
        "source_finding": _source_finding(task),
    }


def _redact_hidden_task_links(row: dict) -> dict:
    """Remove foreign identifiers when this reader cannot see their rows."""
    task = dict(row)
    visible_milestone = task.pop("visible_milestone_id", None)
    visible_engagement = task.pop("visible_engagement_id", None)
    visible_waiting = task.pop("visible_waiting_id", None)
    if task.get("milestone_id") and visible_milestone is None:
        task["milestone_id"] = None
    if task.get("engagement_id") and visible_engagement is None:
        task["engagement_id"] = None
    if task.get("waiting_on_id") and visible_waiting is None:
        task["waiting_on_type"] = None
        task["waiting_on_id"] = None
    return task


def redact_task_relationships(
    rows: list[dict],
    viewer: scope.Viewer,
    resource_filter: Callable[[str, int, dict[str, str]], bool] | None = None,
) -> list[dict]:
    """Redact task links that visibility or composed policy does not permit."""
    tasks = [dict(row) for row in rows]

    def visible_ids(table: str, ids: set[int]) -> set[int]:
        if not ids:
            return set()
        marks = ",".join("?" for _ in ids)
        visible, params = scope.visible_filter(viewer, table)
        return {
            int(row["id"])
            for row in db.query(
                f"SELECT id FROM {table} WHERE id IN ({marks}) AND {visible}",  # noqa: S608 -- closed table set and bound marks
                (*sorted(ids), *params),
            )
        }

    milestones = visible_ids(
        "milestones", {int(row["milestone_id"]) for row in tasks if row.get("milestone_id")}
    )
    engagements = visible_ids(
        "engagements",
        {int(row["engagement_id"]) for row in tasks if row.get("engagement_id")},
    )
    waiting_visible = {
        kind: visible_ids(
            table,
            {
                int(row["waiting_on_id"])
                for row in tasks
                if row.get("waiting_on_type") == kind and row.get("waiting_on_id")
            },
        )
        for kind, table in _WAITING_TABLES.items()
    }
    policy_contexts: dict[tuple[str, int], dict[str, str]] = {}
    if resource_filter is not None:
        from . import policy_context

        resources = {
            *(("milestone", int(row["milestone_id"])) for row in tasks if row.get("milestone_id")),
            *(
                ("engagement", int(row["engagement_id"]))
                for row in tasks
                if row.get("engagement_id")
            ),
            *(
                (str(row["waiting_on_type"]), int(row["waiting_on_id"]))
                for row in tasks
                if row.get("waiting_on_type") in _WAITING_TABLES and row.get("waiting_on_id")
            ),
            *(
                ("finding", int(row["source_finding_id"]))
                for row in tasks
                if row.get("source_finding_id")
            ),
        }
        policy_contexts = policy_context.resource_contexts(list(resources), viewer)

    def policy_permits(entity: str, entity_id: int) -> bool:
        if resource_filter is None:
            return True
        context = policy_contexts.get(
            (entity, entity_id),
            {"relationship_conflict": "true"},
        )
        return resource_filter(entity, entity_id, context)

    for task in tasks:
        milestone_id = int(task.get("milestone_id") or 0)
        if milestone_id and (
            milestone_id not in milestones or not policy_permits("milestone", milestone_id)
        ):
            task["milestone_id"] = None
            task.pop("milestone_title", None)
        engagement_id = int(task.get("engagement_id") or 0)
        if engagement_id and (
            engagement_id not in engagements or not policy_permits("engagement", engagement_id)
        ):
            task["engagement_id"] = None
            task.pop("engagement_name", None)
        kind = task.get("waiting_on_type")
        waiting_id = int(task.get("waiting_on_id") or 0)
        if (
            kind
            and waiting_id
            and (
                waiting_id not in waiting_visible.get(str(kind), set())
                or not policy_permits(str(kind), waiting_id)
            )
        ):
            task["waiting_on_type"] = None
            task["waiting_on_id"] = None
        finding_id = int(task.get("source_finding_id") or 0)
        if finding_id and not policy_permits("finding", finding_id):
            task["source_finding_id"] = None
    return tasks


def filter_task_projection(
    task: dict,
    viewer: scope.Viewer,
    resource_filter: Callable[[str, int, dict[str, str]], bool],
) -> dict:
    """Apply one action's policy to every resource nested in a task view."""
    from . import policy_context

    result = redact_task_relationships([task], viewer, resource_filter)[0]

    def permitted_rows(entity: str, rows: list[dict]) -> list[dict]:
        if not rows:
            return []
        if policy_context.supports_resource(entity):
            contexts = policy_context.resource_contexts(
                [(entity, int(row["id"])) for row in rows], viewer
            )
        else:
            contexts = {
                (entity, int(row["id"])): {
                    "classification": "workspace",
                    "project_type": "",
                }
                for row in rows
            }
        return [
            row
            for row in rows
            if resource_filter(
                entity,
                int(row["id"]),
                contexts.get((entity, int(row["id"])), {"relationship_conflict": "true"}),
            )
        ]

    result["blockers"] = permitted_rows("blocker", list(result.get("blockers") or []))
    policy_downstream = downstream(
        int(result["id"]),
        viewer,
        resource_filter=resource_filter,
    )
    result.update(policy_downstream)
    source = result.get("source_finding")
    if source and not permitted_rows("finding", [source]):
        result["source_finding"] = None
        result["source_finding_id"] = None
    return result


def _source_finding(task: dict) -> dict | None:
    """The finding a task was converted from.

    `findings` carries no tier (scope.UNSCOPED), and the message is written by
    a deterministic rule over rows the rule itself could read — so there is no
    filter to apply, only a lookup.
    """
    fid = task.get("source_finding_id")
    if not fid:
        return None
    return db.query_one("SELECT id, rule_id, severity, message FROM findings WHERE id = ?", (fid,))


def blocking(task_id: int, viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    """The open blockers filed AGAINST this task.

    `blockers.task_id` names the task a blocker BLOCKS, and raise_blocker sets
    that task to 'blocked' (services/blockers.py). Nothing read the edge back,
    so a task could sit in status 'blocked' while every surface that showed it
    — the peek, My Day, Browse — had no way to name what stopped it, who owns
    it, or when it escalates. The reader saw a state with no receipt and had to
    go find the blocker register by hand.

    Viewer-scoped: a blocker nobody may read must not name itself through a
    task they can. Resolved rows are excluded — a settled blocker is history,
    and listing it beside a live one reads as still-stuck.
    """
    frag, vp = scope.visible_filter(viewer, "blockers", alias="b")
    return db.query(
        f"SELECT b.id, b.title, b.owner, b.impact, b.status, b.escalated_at"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" FROM blockers b WHERE b.task_id = ? AND b.status != 'resolved' AND {frag}"
        " ORDER BY CASE b.impact WHEN 'critical' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, b.id",
        (task_id, *vp),
    )


# How deep `downstream` follows the chain. Cycles are closed by the visited
# set in `downstream`, NOT here — this bounds chain LENGTH, so one long chain
# cannot turn a task-peek open into a hundred queries. Ten is far past any
# real chain; the deepest the team has recorded is two. TRUNCATION is
# reported, never arrival: a chain of exactly this many hops is counted in
# full and must not claim it was cut short.
_WAIT_DEPTH = 10


def _blocked_by(task_ids: set[int], viewer: scope.Viewer) -> list[dict]:
    """Tasks waiting directly on any of these tasks.

    `waiting_on: task:N` is the ONLY edge a task completion clears. The other
    two targets do not belong here and the omission is deliberate:

    `blocker:N` looks like a second edge and is not. `blockers.task_id` names
    the task the blocker BLOCKS — raise_blocker sets that task to 'blocked'
    (services/blockers.py) — so a blocker is never caused by a task, and
    counting through it claimed that finishing a task released work when the
    same blocker was what stopped that task from finishing at all. Resolving
    a blocker is a blocker verb, not a task one.

    `promise:N` is settled by a promise verdict, never by finishing a task.
    """
    if not task_ids:
        return []
    frag, vp = scope.visible_filter(viewer, "tasks", alias="t")
    marks = ",".join("?" * len(task_ids))
    return db.query(
        # status != 'done': a finished task is not waiting on anything, and
        # listing it as released work would double-count what already landed
        f"SELECT t.id, t.title, t.status, t.assignee, t.priority"  # noqa: S608 — marks are bound, visible_filter emits only bound marks
        f" FROM tasks t WHERE t.status != 'done' AND {frag}"
        f" AND t.waiting_on_type = 'task' AND t.waiting_on_id IN ({marks})"
        " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, t.id",
        (*vp, *tuple(task_ids)),
    )


def downstream(
    task_id: int,
    viewer: scope.Viewer = scope.NOBODY,
    *,
    resource_filter: Callable[[str, int, dict[str, str]], bool] | None = None,
) -> dict:
    """What finishing this task releases: the tasks waiting on it directly,
    and how many wait behind those.

    `waiting_on` recorded what a task is stuck BEHIND and nothing read the
    other direction, so the edge cost the person who typed it and paid them
    nothing back. This is the payment: "finish this and three people move".

    Viewer-scoped at every hop — a task nobody may read must not be countable
    through the chain either, and a bare count would leak its existence.
    """
    seen: set[int] = {task_id}

    def permitted(rows: list[dict]) -> list[dict]:
        if resource_filter is None or not rows:
            return rows
        from . import policy_context

        contexts = policy_context.resource_contexts(
            [("task", int(row["id"])) for row in rows], viewer
        )
        return [
            row
            for row in rows
            if resource_filter(
                "task",
                int(row["id"]),
                contexts.get(("task", int(row["id"])), {"relationship_conflict": "true"}),
            )
        ]

    direct = permitted(_blocked_by({task_id}, viewer))
    frontier = {t["id"] for t in direct}
    seen |= frontier
    transitive, depth, truncated = len(frontier), 1, False
    while frontier:
        nxt = {t["id"] for t in permitted(_blocked_by(frontier, viewer))} - seen
        # the next hop is read BEFORE the depth test, so `truncated` means work
        # was really left uncounted rather than "the walk reached ten". A chain
        # of exactly _WAIT_DEPTH hops is counted in full, and the peek must not
        # tell that reader the chain runs deeper than it does.
        if not nxt:
            break
        if depth >= _WAIT_DEPTH:
            truncated = True
            break
        seen |= nxt
        transitive += len(nxt)
        frontier, depth = nxt, depth + 1
    return {
        "unblocks": direct,
        "unblocks_total": transitive,
        "depth_capped": truncated,
    }


def list_tasks_joined(viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    """Browse listing: tasks with their milestone title, priority-ordered."""
    # Two filters, two placements. `t` is the LEFT JOIN's driving side, so it
    # belongs in WHERE. `m` is the nullable side and belongs in the ON clause —
    # in WHERE it would drop every task with no milestone and turn the join
    # INNER. Without the `m` filter this column served a private milestone's
    # title beside a workspace task (weekly.week_view has the same pair).
    frag, vp = scope.visible_filter(viewer, "tasks", alias="t")
    mfrag, mp = scope.visible_filter(viewer, "milestones", alias="m")
    efrag, ep = scope.visible_filter(viewer, "engagements", alias="e")
    wtfrag, wtp = scope.visible_filter(viewer, "tasks", alias="waiting_task")
    wbfrag, wbp = scope.visible_filter(viewer, "blockers", alias="waiting_blocker")
    wpfrag, wpp = scope.visible_filter(viewer, "promises", alias="waiting_promise")
    rows = db.query(
        f"SELECT t.*, m.id AS visible_milestone_id, m.title AS milestone_title,"  # noqa: S608 — scope.visible_filter emits only bound marks
        " e.id AS visible_engagement_id,"
        " COALESCE(waiting_task.id, waiting_blocker.id, waiting_promise.id)"
        " AS visible_waiting_id FROM tasks t"
        f" LEFT JOIN milestones m ON m.id = t.milestone_id AND {mfrag}"
        f" LEFT JOIN engagements e ON e.id = t.engagement_id AND {efrag}"
        " LEFT JOIN tasks waiting_task ON t.waiting_on_type = 'task'"
        f" AND waiting_task.id = t.waiting_on_id AND {wtfrag}"
        " LEFT JOIN blockers waiting_blocker ON t.waiting_on_type = 'blocker'"
        f" AND waiting_blocker.id = t.waiting_on_id AND {wbfrag}"
        " LEFT JOIN promises waiting_promise ON t.waiting_on_type = 'promise'"
        f" AND waiting_promise.id = t.waiting_on_id AND {wpfrag}"
        f" WHERE {frag}"
        " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, t.id LIMIT 500",
        (*mp, *ep, *wtp, *wbp, *wpp, *vp),
    )
    return [_redact_hidden_task_links(row) for row in rows]


def list_tasks(
    milestone_id: int = 0,
    status: str = "",
    assignee: str = "",
    viewer: scope.Viewer = scope.NOBODY,
) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "tasks", alias="t")
    mfrag, mp = scope.visible_filter(viewer, "milestones", alias="m")
    efrag, ep = scope.visible_filter(viewer, "engagements", alias="e")
    wtfrag, wtp = scope.visible_filter(viewer, "tasks", alias="waiting_task")
    wbfrag, wbp = scope.visible_filter(viewer, "blockers", alias="waiting_blocker")
    wpfrag, wpp = scope.visible_filter(viewer, "promises", alias="waiting_promise")
    sql = (
        "SELECT t.*, m.id AS visible_milestone_id,"  # noqa: S608 — scope emits bound marks
        " e.id AS visible_engagement_id,"
        " COALESCE(waiting_task.id, waiting_blocker.id, waiting_promise.id)"
        " AS visible_waiting_id FROM tasks t"
        f" LEFT JOIN milestones m ON m.id = t.milestone_id AND {mfrag}"
        f" LEFT JOIN engagements e ON e.id = t.engagement_id AND {efrag}"
        " LEFT JOIN tasks waiting_task ON t.waiting_on_type = 'task'"
        f" AND waiting_task.id = t.waiting_on_id AND {wtfrag}"
        " LEFT JOIN blockers waiting_blocker ON t.waiting_on_type = 'blocker'"
        f" AND waiting_blocker.id = t.waiting_on_id AND {wbfrag}"
        " LEFT JOIN promises waiting_promise ON t.waiting_on_type = 'promise'"
        f" AND waiting_promise.id = t.waiting_on_id AND {wpfrag}"
        f" WHERE {frag}"
    )
    params: list[str | int] = [*mp, *ep, *wtp, *wbp, *wpp, *vp]
    if milestone_id:
        sql += " AND m.id = ?"
        params.append(milestone_id)
    if status:
        sql += " AND t.status = ?"
        params.append(status)
    if assignee:
        sql += " AND t.assignee = ?"
        params.append(assignee)
    sql += (
        " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, t.id"
        " LIMIT 500"  # Browse renders these unpaginated — bound the dump
    )
    return [_redact_hidden_task_links(row) for row in db.query(sql, tuple(params))]
