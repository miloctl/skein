"""Milestone/task tools — thin wrappers over app.services.work.

Mutations respect the review gate: with STRANDS_AGENT_REVIEW=1 the agent's
writes become pending_changes proposals a human approves in the review inbox.
"""

import json
from typing import Any

from strands import tool

from ..services import work
from ._gate import gated_write


@tool
def create_milestone(
    title: str, description: str = "", project: str = "default", owner: str = "", due_date: str = ""
) -> str:
    """Create a project milestone.

    Args:
        title: Short name of the milestone.
        description: What "done" looks like for this milestone.
        project: Project/engagement the milestone belongs to.
        owner: Team member (human or agent) responsible.
        due_date: Target date in YYYY-MM-DD format, or empty if none.
    """
    payload = {
        "title": title,
        "description": description,
        "project": project,
        "owner": owner,
        "due_date": due_date,
    }
    return gated_write(
        "milestone",
        "create",
        dict(payload),
        lambda: work.create_milestone(**payload, actor="agent", origin="agent"),
    )


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
    payload = {
        "status": status,
        "title": title,
        "description": description,
        "owner": owner,
        "due_date": due_date,
    }
    payload = {k: v for k, v in payload.items() if v}
    return gated_write(
        "milestone",
        "update",
        payload,
        lambda: work.update_milestone(milestone_id, **payload, actor="agent", origin="agent"),
        entity_id=milestone_id,
    )


@tool
def list_milestones(project: str = "", status: str = "") -> str:
    """List milestones, optionally filtered by project and/or status.

    Args:
        project: Filter to one project (empty for all).
        status: Filter to one status (empty for all).
    """
    return json.dumps(work.list_milestones(project, status))


@tool
def create_task(
    title: str,
    description: str = "",
    milestone_id: int = 0,
    assignee: str = "",
    priority: str = "medium",
    due_date: str = "",
    engagement_id: int = 0,
) -> str:
    """Create a task, optionally attached to a milestone or an engagement.

    Args:
        title: Short name of the task.
        description: Details of the work.
        milestone_id: Parent milestone ID, or 0 for none.
        assignee: Team member (human or agent) doing the work.
        priority: One of low, medium, high, urgent.
        due_date: Target date in YYYY-MM-DD format, or empty if none.
        engagement_id: Engagement to link the task to directly (0 for none) —
            use when the work belongs to an engagement but no milestone fits.
    """
    payload: dict[str, Any] = {
        "title": title,
        "description": description,
        "milestone_id": milestone_id,
        "assignee": assignee,
        "priority": priority,
        "due_date": due_date,
        "engagement_id": engagement_id,
    }
    return gated_write(
        "task",
        "create",
        dict(payload),
        lambda: work.create_task(**payload, actor="agent", origin="agent"),
    )


@tool
def update_task(
    task_id: int,
    status: str = "",
    assignee: str = "",
    priority: str = "",
    due_date: str = "",
    description: str = "",
    waiting_on: str = "",
) -> str:
    """Update fields on an existing task. Only pass the fields to change.

    Args:
        task_id: ID of the task.
        status: One of todo, in_progress, blocked, done.
        assignee: New assignee.
        priority: One of low, medium, high, urgent.
        due_date: New due date (YYYY-MM-DD).
        description: New description.
        waiting_on: What this task is stuck behind — 'task:12', 'blocker:3',
            or 'commitment:7'; '-' clears it.
    """
    payload = {
        "status": status,
        "assignee": assignee,
        "priority": priority,
        "due_date": due_date,
        "description": description,
        "waiting_on": waiting_on,
    }
    payload = {k: v for k, v in payload.items() if v}
    return gated_write(
        "task",
        "update",
        payload,
        lambda: work.update_task(task_id, **payload, actor="agent", origin="agent"),
        entity_id=task_id,
    )


@tool
def list_tasks(milestone_id: int = 0, status: str = "", assignee: str = "") -> str:
    """List tasks, optionally filtered by milestone, status, and/or assignee.

    Args:
        milestone_id: Filter to one milestone (0 for all).
        status: Filter to one status (empty for all).
        assignee: Filter to one assignee (empty for all).
    """
    return json.dumps(work.list_tasks(milestone_id, status, assignee))
