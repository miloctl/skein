"""Milestone and task services — the single write path for both REST and tools."""

from .. import db
from .search import index_record

MILESTONE_STATUSES = ("planned", "in_progress", "blocked", "done")
TASK_STATUSES = ("todo", "in_progress", "blocked", "done")
PRIORITIES = ("low", "medium", "high", "urgent")


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
    ts = db.now()
    mid = db.execute(
        "INSERT INTO milestones (project, title, description, owner, due_date,"
        " origin, created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (project, title, description, owner, due_date or None, origin, actor, ts, ts),
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
    fields = {k: v for k, v in
              [("status", status), ("title", title), ("description", description),
               ("owner", owner), ("due_date", due_date)] if v}
    if not fields:
        raise ValueError("nothing to update")
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE milestones SET {sets}, updated_at = ? WHERE id = ?",
        (*fields.values(), db.now(), milestone_id),
    )
    db.log_activity(actor, "update_milestone", f"#{milestone_id} {status or 'edited'}")
    row = db.query_one("SELECT * FROM milestones WHERE id = ?", (milestone_id,))
    if row:
        index_record("milestone", milestone_id, row["title"],
                     f"{row['description']} {row['project']} {row['owner']}")
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
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    ts = db.now()
    tid = db.execute(
        "INSERT INTO tasks (milestone_id, title, description, assignee, priority,"
        " due_date, origin, created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (milestone_id or None, title, description, assignee, priority,
         due_date or None, origin, actor, ts, ts),
    )
    db.log_activity(actor, "create_task", f"#{tid} {title}")
    index_record("task", tid, title, f"{description} {assignee}")
    return {"id": tid, "title": title, "status": "todo"}


def update_task(
    task_id: int,
    status: str = "",
    assignee: str = "",
    priority: str = "",
    due_date: str = "",
    description: str = "",
    title: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    if status and status not in TASK_STATUSES:
        raise ValueError(f"status must be one of {TASK_STATUSES}")
    if priority and priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    fields = {k: v for k, v in
              [("status", status), ("assignee", assignee), ("priority", priority),
               ("due_date", due_date), ("description", description), ("title", title)] if v}
    if not fields:
        raise ValueError("nothing to update")
    sets = ", ".join(f"{k} = ?" for k in fields)
    db.execute(
        f"UPDATE tasks SET {sets}, updated_at = ? WHERE id = ?",
        (*fields.values(), db.now(), task_id),
    )
    db.log_activity(actor, "update_task", f"#{task_id} {status or 'edited'}")
    row = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if row:
        index_record("task", task_id, row["title"], f"{row['description']} {row['assignee']}")
    return {"id": task_id, "updated": list(fields)}


def list_tasks(milestone_id: int = 0, status: str = "", assignee: str = "") -> list[dict]:
    sql, params = "SELECT * FROM tasks WHERE 1=1", []
    if milestone_id:
        sql += " AND milestone_id = ?"
        params.append(milestone_id)
    if status:
        sql += " AND status = ?"
        params.append(status)
    if assignee:
        sql += " AND assignee = ?"
        params.append(assignee)
    sql += (" ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
            " WHEN 'medium' THEN 2 ELSE 3 END, id")
    return db.query(sql, tuple(params))
