"""Milestone and task services — the single write path for both REST and tools."""

import re

from .. import db
from . import scope
from .search import index_record

MILESTONE_STATUSES = ("planned", "in_progress", "blocked", "done")
TASK_STATUSES = ("todo", "in_progress", "blocked", "done")
PRIORITIES = ("low", "medium", "high", "urgent")
WEEK_RE = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")


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
    db.validate_date("due_date", due_date, allow_clear=False)
    ts = db.now()
    # resolve the engagement link at write time — the name join is display
    # only, the id is what health/forecast/handoff trust
    eng = db.query_one("SELECT id FROM engagements WHERE name = ?", (project,))
    # the membership check belongs INSIDE the insert's transaction — bare, it
    # opens its own connection, so a person removed from the crew between the
    # check and the write still scopes the row (services/scope.py::resolve_write)
    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor)
        mid = db.execute(
            "INSERT INTO milestones (project, engagement_id, title, description, owner,"
            " due_date, origin, created_by, created_at, updated_at, visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                f"Milestone #{mid} '{title}' names project '{project}' but no engagement"
                " matches — it will not count in health/forecast until you relink it.",
                tier="digest",
                link="/dashboard",
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
    db.validate_date("due_date", due_date)
    current = db.query_one("SELECT * FROM milestones WHERE id = ?", (milestone_id,))
    if not current:
        # scope.missing, not missing_text: this is the row in the PATH, so it
        # is a 404. The link probes below name a row in the BODY and stay 400.
        raise scope.missing("milestones", milestone_id)
    scope.assert_editable("milestones", current, actor, verb="update")
    fields: dict[str, str | None] = {
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
    if engagement_id:
        # mislinked work silently drops out of health/forecast/handoff —
        # the link must be repairable, not set-once (-1 unlinks)
        efrag, ep = scope.visible_filter(scope.Viewer.for_actor(actor), "engagements")
        if engagement_id > 0 and not db.query_one(
            f"SELECT id FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
            (engagement_id, *ep),
        ):
            raise ValueError(scope.missing_text("engagements", engagement_id))
        fields["engagement_id"] = None if engagement_id < 0 else engagement_id  # type: ignore[assignment]
    if not fields:
        raise ValueError("nothing to update")
    for clearable, empty in (("due_date", None), ("owner", ""), ("description", "")):
        if fields.get(clearable) == "-":
            fields[clearable] = empty
    current = db.query_one("SELECT status FROM milestones WHERE id = ?", (milestone_id,))
    if status == "done" and current and current["status"] != "done":
        fields["completed_at"] = db.now()  # slip forecast reads this, not updated_at
    elif status and status != "done" and current and current["status"] == "done":
        fields["completed_at"] = None
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE milestones SET {sets}, updated_at = ? WHERE id = ?",  # noqa: S608 — keys hardcoded
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
    frag, vp = scope.visible_filter(viewer, "milestones")
    sql, params = (
        f"SELECT * FROM milestones WHERE {frag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        list(vp),
    )
    if project:
        sql += " AND project = ?"
        params.append(project)
    if status:
        sql += " AND status = ?"
        params.append(status)
    return db.query(sql + " ORDER BY due_date IS NULL, due_date, id LIMIT 500", tuple(params))


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
) -> dict:
    if not title.strip():
        raise ValueError("task title is required")
    db.validate_date("due_date", due_date, allow_clear=False)
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    # scope.Viewer.for_actor, not a bare id probe: an unfiltered existence
    # check accepts a scoped id and rejects an absent one, and ids are
    # sequential — so the two answers enumerate the private rows.
    av = scope.Viewer.for_actor(actor)
    mfrag, mp = scope.visible_filter(av, "milestones")
    if milestone_id and not db.query_one(
        f"SELECT id FROM milestones WHERE id = ? AND {mfrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (milestone_id, *mp),
    ):
        raise ValueError(scope.missing_text("milestones", milestone_id))
    efrag, ep = scope.visible_filter(av, "engagements")
    if engagement_id and not db.query_one(
        f"SELECT id FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (engagement_id, *ep),
    ):
        raise ValueError(scope.missing_text("engagements", engagement_id))
    ts = db.now()
    with db.transaction():
        tier, cid = scope.resolve_write(visibility, crew_id, actor=actor)
        scope.assert_readable_by(tier, cid, assignee, label="assignee", author=actor)
        tid = db.execute(
            "INSERT INTO tasks (milestone_id, engagement_id, title, description, assignee,"
            " priority, due_date, origin, created_by, created_at, updated_at,"
            " visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
    from .mentions import scan

    # title too: a short `todo: ask @mira ...` capture lands entirely in the
    # title, and a mention there must ping like one in the description
    scan("task", tid, f"{title} {description}", actor=actor, link="/dashboard")
    return {"id": tid, "title": title, "status": "todo"}


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
) -> dict:
    if status and status not in TASK_STATUSES:
        raise ValueError(f"status must be one of {TASK_STATUSES}")
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
        table = _WAITING_TABLES[kind]
        wfrag, wp = scope.visible_filter(scope.Viewer.for_actor(actor), table)
        if not db.query_one(
            f"SELECT id FROM {table} WHERE id = ? AND {wfrag}",  # noqa: S608 — table from _WAITING_TABLES, and scope.visible_filter emits only bound marks
            (waiting_id, *wp),
        ):
            raise ValueError(scope.missing_text(table, waiting_id))
    current = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not current:
        raise scope.missing("tasks", task_id)
    scope.assert_editable("tasks", current, actor, verb="update")
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
    for link_field, link_id, table in (
        ("milestone_id", milestone_id, "milestones"),
        ("engagement_id", engagement_id, "engagements"),
    ):
        if link_id:
            lfrag, lp = scope.visible_filter(scope.Viewer.for_actor(actor), table)
            if link_id > 0 and not db.query_one(
                f"SELECT id FROM {table} WHERE id = ? AND {lfrag}",  # noqa: S608 — table hardcoded, and scope.visible_filter emits only bound marks
                (link_id, *lp),
            ):
                raise ValueError(scope.missing_text(table, link_id))
            fields[link_field] = None if link_id < 0 else link_id
    if not fields:
        raise ValueError("nothing to update")
    if committed_week == "-":
        fields["committed_week"] = None
    # "-" clears any clearable field — the single write path must be able to
    # unset a wrong due date without hand-editing SQLite
    for clearable, empty in (("due_date", None), ("assignee", ""), ("description", "")):
        if fields.get(clearable) == "-":
            fields[clearable] = empty
    if status == "done" and current["status"] != "done":
        fields["completed_at"] = db.now()  # flow metrics read this, not updated_at
    elif status and status != "done" and current["status"] == "done":
        fields["completed_at"] = None
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
    db.execute(
        f"UPDATE tasks SET {sets}, updated_at = ? WHERE id = ?",  # noqa: S608 — keys hardcoded
        (*fields.values(), db.now(), task_id),
    )
    db.log_activity(actor, "update_task", f"#{task_id} {status or 'edited'}{note}")
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
    return {"id": task_id, "updated": list(fields)}


def list_tasks_joined(viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    """Browse listing: tasks with their milestone title, priority-ordered."""
    # Two filters, two placements. `t` is the LEFT JOIN's driving side, so it
    # belongs in WHERE. `m` is the nullable side and belongs in the ON clause —
    # in WHERE it would drop every task with no milestone and turn the join
    # INNER. Without the `m` filter this column served a private milestone's
    # title beside a workspace task (weekly.week_view has the same pair).
    frag, vp = scope.visible_filter(viewer, "tasks", alias="t")
    mfrag, mp = scope.visible_filter(viewer, "milestones", alias="m")
    return db.query(
        f"SELECT t.*, m.title AS milestone_title FROM tasks t"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" LEFT JOIN milestones m ON m.id = t.milestone_id AND {mfrag}"
        f" WHERE {frag}"
        " ORDER BY CASE t.priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, t.id LIMIT 500",
        (*mp, *vp),
    )


def list_tasks(
    milestone_id: int = 0,
    status: str = "",
    assignee: str = "",
    viewer: scope.Viewer = scope.NOBODY,
) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "tasks")
    sql = f"SELECT * FROM tasks WHERE {frag}"  # noqa: S608 — scope.visible_filter emits only bound marks
    params: list[str | int] = list(vp)
    if milestone_id:
        sql += " AND milestone_id = ?"
        params.append(milestone_id)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if assignee:
        sql += " AND assignee = ?"
        params.append(assignee)
    sql += (
        " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, id"
        " LIMIT 500"  # Browse renders these unpaginated — bound the dump
    )
    return db.query(sql, tuple(params))
