"""Milestone and task services — the single write path for both REST and tools."""

import re

from .. import db
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
) -> dict:
    if not title.strip():
        raise ValueError("milestone title is required")
    ts = db.now()
    # resolve the engagement link at write time — the name join is display
    # only, the id is what health/forecast/handoff should trust
    eng = db.query_one("SELECT id FROM engagements WHERE name = ?", (project,))
    mid = db.execute(
        "INSERT INTO milestones (project, engagement_id, title, description, owner,"
        " due_date, origin, created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
        ),
    )
    db.log_activity(actor, "create_milestone", f"#{mid} {title}")
    index_record("milestone", mid, title, f"{description} {project} {owner}")
    return {"id": mid, "title": title, "status": "planned"}


def update_milestone(
    milestone_id: int,
    status: str = "",
    title: str = "",
    description: str = "",
    owner: str = "",
    due_date: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    if status and status not in MILESTONE_STATUSES:
        raise ValueError(f"status must be one of {MILESTONE_STATUSES}")
    if not db.query_one("SELECT id FROM milestones WHERE id = ?", (milestone_id,)):
        raise ValueError(f"milestone #{milestone_id} not found")
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
    if not fields:
        raise ValueError("nothing to update")
    for clearable, empty in (("due_date", None), ("owner", ""), ("description", "")):
        if fields.get(clearable) == "-":
            fields[clearable] = empty
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


def list_milestones(project: str = "", status: str = "") -> list[dict]:
    sql, params = "SELECT * FROM milestones WHERE 1=1", []
    if project:
        sql += " AND project = ?"
        params.append(project)
    if status:
        sql += " AND status = ?"
        params.append(status)
    return db.query(sql + " ORDER BY due_date IS NULL, due_date, id", tuple(params))


def create_task(
    title: str,
    description: str = "",
    milestone_id: int = 0,
    assignee: str = "",
    priority: str = "medium",
    due_date: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    if not title.strip():
        raise ValueError("task title is required")
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    if milestone_id and not db.query_one("SELECT id FROM milestones WHERE id = ?", (milestone_id,)):
        raise ValueError(f"milestone #{milestone_id} not found")
    ts = db.now()
    tid = db.execute(
        "INSERT INTO tasks (milestone_id, title, description, assignee, priority,"
        " due_date, origin, created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            milestone_id or None,
            title,
            description,
            assignee,
            priority,
            due_date or None,
            origin,
            actor,
            ts,
            ts,
        ),
    )
    db.log_activity(actor, "create_task", f"#{tid} {title}")
    index_record("task", tid, title, f"{description} {assignee}")
    return {"id": tid, "title": title, "status": "todo"}


WAITING_ON_TYPES = ("task", "blocker", "commitment")
_WAITING_TABLES = {"task": "tasks", "blocker": "blockers", "commitment": "commitments"}


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
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    if status and status not in TASK_STATUSES:
        raise ValueError(f"status must be one of {TASK_STATUSES}")
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
        if kind not in WAITING_ON_TYPES or not ref.strip().lstrip("#").isdigit():
            raise ValueError(
                f"waiting_on must look like 'task:12', 'blocker:3', or"
                f" 'commitment:7' (one of {WAITING_ON_TYPES}), or '-' to clear"
            )
        waiting_type, waiting_id = kind, int(ref.strip().lstrip("#"))
        if kind == "task" and waiting_id == task_id:
            raise ValueError("a task cannot wait on itself")
        table = _WAITING_TABLES[kind]
        if not db.query_one(f"SELECT id FROM {table} WHERE id = ?", (waiting_id,)):  # noqa: S608
            raise ValueError(f"{kind} #{waiting_id} not found")
    current = db.query_one("SELECT status, delegated_agent FROM tasks WHERE id = ?", (task_id,))
    if not current:
        raise ValueError(f"task #{task_id} not found")
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
        ]
        if v
    }
    if waiting_on == "-":
        fields["waiting_on_type"] = None
        fields["waiting_on_id"] = None
    elif waiting_type:
        fields["waiting_on_type"] = waiting_type
        fields["waiting_on_id"] = waiting_id
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
    db.log_activity(actor, "update_task", f"#{task_id} {status or 'edited'}")
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if row:
        index_record("task", task_id, row["title"], f"{row['description']} {row['assignee']}")
    return {"id": task_id, "updated": list(fields)}


def list_tasks(milestone_id: int = 0, status: str = "", assignee: str = "") -> list[dict]:
    sql = "SELECT * FROM tasks WHERE 1=1"
    params: list[str | int] = []
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
    )
    return db.query(sql, tuple(params))
