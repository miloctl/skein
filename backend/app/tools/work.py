"""Milestone/task tools — thin wrappers over app.services.work.

Every mutation routes through tools/_gate.py: the (agent, entity) authority
level decides direct write vs review proposal, and SKEIN_AGENT_REVIEW=1
governs only the default "review" level.
"""

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
from ..services import scope, work
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
    payload: dict[str, Any] = {
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
        lambda: work.create_milestone(**payload, actor=agent_identity(), origin="agent"),
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
    payload: dict[str, Any] = {
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
        lambda: work.update_milestone(
            milestone_id, **payload, actor=agent_identity(), origin="agent"
        ),
        entity_id=milestone_id,
    )


@tool
def list_milestones(project: str = "", status: str = "") -> str:
    """List milestones, optionally filtered by project and/or status.

    Args:
        project: Filter to one project (empty for all).
        status: Filter to one status (empty for all).
    """
    with db.read_transaction():
        rows = work.list_milestones(project, status)
        contexts = work.milestone_collection_policy_contexts(rows, scope.NOBODY)
        subject = current_policy_subject()
        engine = current_policy_engine()
        permitted = []
        for row in rows:
            attributes = contexts[int(row["id"])]
            decision = engine.decide(
                PolicyInput(
                    subject,
                    "skein.tool.list_milestones",
                    PolicyResource(
                        "milestone",
                        str(row["id"]),
                        attributes["project_type"],
                        attributes["classification"],
                        attributes,
                    ),
                    "agent_tool",
                    agent=agent_identity(),
                    tool="list_milestones",
                    tool_effect="read",
                    tool_risk="low",
                )
            )
            if decision.effect == PolicyEffect.PERMIT:
                permitted.append(row)
        return json.dumps(permitted)


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
        lambda: work.create_task(**payload, actor=agent_identity(), origin="agent"),
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
            or 'promise:7'; '-' clears it.
    """
    payload: dict[str, Any] = {
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
        lambda: work.update_task(task_id, **payload, actor=agent_identity(), origin="agent"),
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
    with db.read_transaction():
        rows = work.list_tasks(milestone_id, status, assignee)
        contexts = work.task_collection_policy_contexts(rows, scope.NOBODY)
        subject = current_policy_subject()
        engine = current_policy_engine()
        permitted = []
        for row in rows:
            attributes = contexts[int(row["id"])]
            decision = engine.decide(
                PolicyInput(
                    subject,
                    "skein.tool.list_tasks",
                    PolicyResource(
                        "task",
                        str(row["id"]),
                        attributes["project_type"],
                        attributes["classification"],
                        attributes,
                    ),
                    "agent_tool",
                    agent=agent_identity(),
                    tool="list_tasks",
                    tool_effect="read",
                    tool_risk="low",
                )
            )
            if decision.effect == PolicyEffect.PERMIT:
                permitted.append(row)
        return json.dumps(permitted)
