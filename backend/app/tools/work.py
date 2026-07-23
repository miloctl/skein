"""Milestone and task tools exposed to the agent."""

import json

from strands import tool

from .. import db

MILESTONE_STATUSES = ("planned", "in_progress", "blocked", "done")
TASK_STATUSES = ("todo", "in_progress", "blocked", "done")
PRIORITIES = ("low", "medium", "high", "urgent")


@tool
def create_milestone(
    title: str,
    description: str = "",
    project: str = "default",
    owner: str = "",
    due_date: str = "",
) -> str:
    """Create a project milestone.

    Args:
        title: Short name of the milestone.
        description: What "done" looks like for this milestone.
        project: Project the milestone belongs to.
        owner: Team member (human or agent) responsible.
        due_date: Target date in YYYY-MM-DD format, or empty if none.
    """
    ts = db.now()
    mid = db.execute(
        "INSERT INTO milestones (project, title, description, owner, due_date, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (project, title, description, owner, due_date or None, ts, ts),
    )
    db.log_activity("agent", "create_milestone", f"#{mid} {title}")
    return json.dumps({"id": mid, "title": title, "status": "planned"})


@tool
def update_milestone(
    milestone_id: int,
    status: str = "",
    title: str = "",
    description: str = "",
    owner: str = "",
    due_date: str = "",
) -> str:
    """Update fields on an existing milestone. Only pass the fields to change.

    Args:
        milestone_id: ID of the milestone.
        status: One of planned, in_progress, blocked, done.
        title: New title.
        description: New description.
        owner: New owner.
        due_date: New due date (YYYY-MM-DD).
    """
    if status and status not in MILESTONE_STATUSES:
        return json.dumps({"error": f"status must be one of {MILESTONE_STATUSES}"})
    fields, params = [], []
    for col, val in (
        ("status", status), ("title", title), ("description", description),
        ("owner", owner), ("due_date", due_date),
    ):
        if val:
            fields.append(f"{col} = ?")
            params.append(val)
    if not fields:
        return json.dumps({"error": "nothing to update"})
    params += [db.now(), milestone_id]
    db.execute(f"UPDATE milestones SET {', '.join(fields)}, updated_at = ? WHERE id = ?", tuple(params))
    db.log_activity("agent", "update_milestone", f"#{milestone_id} {status or 'edited'}")
    return json.dumps({"id": milestone_id, "updated": [f.split(' ')[0] for f in fields]})


@tool
def list_milestones(project: str = "", status: str = "") -> str:
    """List milestones, optionally filtered by project and/or status.

    Args:
        project: Filter to one project (empty for all).
        status: Filter to one status (empty for all).
    """
    sql, params = "SELECT * FROM milestones WHERE 1=1", []
    if project:
        sql += " AND project = ?"
        params.append(project)
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY due_date IS NULL, due_date, id"
    return json.dumps(db.query(sql, tuple(params)))


@tool
def create_task(
    title: str,
    description: str = "",
    milestone_id: int = 0,
    assignee: str = "",
    priority: str = "medium",
    due_date: str = "",
) -> str:
    """Create a task, optionally attached to a milestone.

    Args:
        title: Short name of the task.
        description: Details of the work.
        milestone_id: Parent milestone ID, or 0 for none.
        assignee: Team member (human or agent) doing the work.
        priority: One of low, medium, high, urgent.
        due_date: Target date in YYYY-MM-DD format, or empty if none.
    """
    if priority not in PRIORITIES:
        return json.dumps({"error": f"priority must be one of {PRIORITIES}"})
    ts = db.now()
    tid = db.execute(
        "INSERT INTO tasks (milestone_id, title, description, assignee, priority, due_date, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (milestone_id or None, title, description, assignee, priority, due_date or None, ts, ts),
    )
    db.log_activity("agent", "create_task", f"#{tid} {title}")
    return json.dumps({"id": tid, "title": title, "status": "todo"})


@tool
def update_task(
    task_id: int,
    status: str = "",
    assignee: str = "",
    priority: str = "",
    due_date: str = "",
    description: str = "",
) -> str:
    """Update fields on an existing task. Only pass the fields to change.

    Args:
        task_id: ID of the task.
        status: One of todo, in_progress, blocked, done.
        assignee: New assignee.
        priority: One of low, medium, high, urgent.
        due_date: New due date (YYYY-MM-DD).
        description: New description.
    """
    if status and status not in TASK_STATUSES:
        return json.dumps({"error": f"status must be one of {TASK_STATUSES}"})
    if priority and priority not in PRIORITIES:
        return json.dumps({"error": f"priority must be one of {PRIORITIES}"})
    fields, params = [], []
    for col, val in (
        ("status", status), ("assignee", assignee), ("priority", priority),
        ("due_date", due_date), ("description", description),
    ):
        if val:
            fields.append(f"{col} = ?")
            params.append(val)
    if not fields:
        return json.dumps({"error": "nothing to update"})
    params += [db.now(), task_id]
    db.execute(f"UPDATE tasks SET {', '.join(fields)}, updated_at = ? WHERE id = ?", tuple(params))
    db.log_activity("agent", "update_task", f"#{task_id} {status or 'edited'}")
    return json.dumps({"id": task_id, "updated": [f.split(' ')[0] for f in fields]})


@tool
def list_tasks(milestone_id: int = 0, status: str = "", assignee: str = "") -> str:
    """List tasks, optionally filtered by milestone, status, and/or assignee.

    Args:
        milestone_id: Filter to one milestone (0 for all).
        status: Filter to one status (empty for all).
        assignee: Filter to one assignee (empty for all).
    """
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
    sql += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, id"
    return json.dumps(db.query(sql, tuple(params)))
