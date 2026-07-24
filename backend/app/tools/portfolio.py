"""Round-3 tools: portfolio reads, commitments, delegation, decision chains,
context pack, agent inbox."""

import json
from typing import Any

from strands import tool

from ..services import collab, commitments, context_pack, delegation, portfolio
from ._gate import gated_write


@tool
def get_portfolio_health() -> str:
    """Engagement health (red/yellow/green) with the receipts behind each
    verdict: overdue milestones, open/escalated blockers, stale work."""
    return json.dumps(portfolio.engagement_health())


@tool
def get_flow_metrics() -> str:
    """Team flow metrics from real timestamps: cycle time, weekly throughput,
    WIP per person, and stale in-progress tasks."""
    return json.dumps(portfolio.flow_metrics())


@tool
def what_if_staffing(request_id: int, people: str, percent: int = 50) -> str:
    """Project capacity impact of accepting an intake request.

    Args:
        request_id: Intake request ID.
        people: Comma-separated names who would staff it.
        percent: Allocation each would take (1-100).
    """
    names = [p.strip() for p in people.split(",") if p.strip()]
    try:
        return json.dumps(portfolio.what_if(request_id, names, percent))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@tool
def add_commitment(
    promise: str, to_whom: str = "", due_date: str = "", engagement_id: int = 0
) -> str:
    """Record an external commitment (a promise made to someone outside the
    team) so the exec readout tracks it.

    Args:
        promise: What was promised.
        to_whom: Who it was promised to.
        due_date: When it's due (YYYY-MM-DD).
        engagement_id: Related engagement, or 0.
    """
    payload: dict[str, Any] = {
        "promise": promise,
        "to_whom": to_whom,
        "due_date": due_date,
        "engagement_id": engagement_id,
    }
    return gated_write(
        "commitment",
        "create",
        payload,
        lambda: commitments.add_commitment(**payload, actor="agent", origin="agent"),
    )


@tool
def list_commitments(status: str = "") -> str:
    """List external commitments the team has made.

    Args:
        status: open, kept, missed, withdrawn, or empty for all.
    """
    return json.dumps(commitments.list_commitments(status))


@tool
def delegate_task(task_id: int, agent: str, sponsor: str) -> str:
    """Delegate a task to an AI agent with a human sponsor who stays
    accountable for it.

    Args:
        task_id: The task to delegate.
        agent: Agent identity that will do the work.
        sponsor: Human teammate accountable for the outcome.
    """
    payload: dict[str, Any] = {"task_id": task_id, "agent": agent, "sponsor": sponsor}
    return gated_write(
        "delegation",
        "create",
        payload,
        summary=f"delegate task #{task_id} to {agent}",
        direct=lambda: delegation.delegate_task(**payload, actor="agent", origin="agent"),
    )


@tool
def supersede_decision(
    decision_id: int, title: str, decision: str, context: str = "", review_by: str = ""
) -> str:
    """Replace a standing decision with a new one, keeping the chain — use
    instead of recording a contradicting decision.

    Args:
        decision_id: The decision being replaced.
        title: Title of the new decision.
        decision: The new decision text.
        context: Why it changed.
        review_by: Date (YYYY-MM-DD) when the new decision should be re-reviewed.
    """
    payload = {"title": title, "decision": decision, "context": context, "review_by": review_by}
    return gated_write(
        "decision",
        "update",
        payload,
        entity_id=decision_id,
        summary=f"supersede decision #{decision_id}: {title}",
        direct=lambda: collab.supersede_decision(
            decision_id, **payload, actor="agent", origin="agent"
        ),
    )


@tool
def get_context_pack() -> str:
    """The team context pack (org-brain): active decisions, engagement state,
    lessons, conventions. Load this before working on anything team-related."""
    return json.dumps(context_pack.get_pack(actor="agent"))


@tool
def my_agent_inbox(agent: str = "agent") -> str:
    """Ambient inbox for an agent identity: delegated tasks, questions
    assigned to it, rejected proposals (with reviewer notes), notifications.

    Args:
        agent: The agent identity to check (default 'agent').
    """
    from ..services.users import ensure_user

    ensure_user(agent, kind="agent")
    return json.dumps(delegation.agent_inbox(agent))
