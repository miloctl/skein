"""Platform tools: blockers, intake, engagements, playbooks, handoffs, search."""

import json
from typing import Any

from strands import tool

from ..services import blockers, engagements, handoff, intake, playbooks, search
from ._gate import gated_write


def _safe(fn):
    try:
        return json.dumps(fn())
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@tool
def raise_blocker(
    title: str, detail: str = "", owner: str = "", impact: str = "medium", task_id: int = 0
) -> str:
    """File a blocker in the register so it gets tracked and escalated if it ages.

    Args:
        title: Short description of what's blocked.
        detail: Context: what's needed to unblock.
        owner: Who owns resolving it.
        impact: One of low, medium, high, critical (drives escalation speed).
        task_id: Related task ID, or 0 (marks that task blocked too).
    """
    payload: dict[str, Any] = {
        "title": title,
        "detail": detail,
        "owner": owner,
        "impact": impact,
        "task_id": task_id,
    }
    return gated_write(
        "blocker",
        "create",
        payload,
        lambda: blockers.raise_blocker(**payload, actor="agent", origin="agent"),
    )


@tool
def resolve_blocker(blocker_id: int, resolution: str = "") -> str:
    """Mark a blocker resolved.

    Args:
        blocker_id: ID of the blocker.
        resolution: How it was resolved.
    """
    payload = {"resolution": resolution}
    return gated_write(
        "blocker",
        "update",
        payload,
        lambda: blockers.resolve_blocker(blocker_id, **payload, actor="agent", origin="agent"),
        entity_id=blocker_id,
    )


@tool
def list_blockers(status: str = "", owner: str = "") -> str:
    """List blockers (unresolved by default), most severe first.

    Args:
        status: 'open', 'escalated', 'resolved', or empty for all unresolved.
        owner: Filter to one owner.
    """
    return json.dumps(blockers.list_blockers(status, owner))


@tool
def submit_intake_request(
    title: str, detail: str = "", requester: str = "", project_class: str = ""
) -> str:
    """Submit a new engagement request to the team's intake queue.

    Args:
        title: What is being asked of the team.
        detail: Context, goals, constraints.
        requester: Who is asking.
        project_class: prototype, incident, migration, or other class if known.
    """
    payload = {
        "title": title,
        "detail": detail,
        "requester": requester,
        "project_class": project_class,
    }
    return gated_write(
        "intake",
        "create",
        payload,
        lambda: intake.submit_request(**payload, actor="agent", origin="agent"),
    )


@tool
def list_intake_requests(status: str = "") -> str:
    """List intake requests with their triage scores and dispositions.

    Args:
        status: submitted, scored, accepted, deferred, declined, or empty for all.
    """
    return json.dumps(intake.list_requests(status))


@tool
def list_engagements(status: str = "") -> str:
    """List engagements (the team's active bodies of work) with allocations.

    Args:
        status: proposed, active, closing, closed, or empty for all.
    """
    return json.dumps(engagements.list_engagements(status))


@tool
def team_capacity() -> str:
    """Show total allocation percent per person across active engagements
    (over 100 means overcommitted). Use before accepting new work."""
    return json.dumps(engagements.capacity())


@tool
def record_lesson(
    lesson: str, recommendation: str = "", engagement_id: int = 0, project_class: str = "general"
) -> str:
    """Record a retro lesson tagged by project class; future playbook
    instantiations of that class surface it at kickoff.

    Args:
        lesson: What was learned.
        recommendation: What to do differently next time.
        engagement_id: The engagement it came from, or 0.
        project_class: Class the lesson applies to (prototype, incident, migration, general).
    """
    payload: dict[str, Any] = {
        "lesson": lesson,
        "recommendation": recommendation,
        "engagement_id": engagement_id,
        "project_class": project_class,
    }
    return gated_write(
        "lesson",
        "create",
        payload,
        lambda: engagements.record_lesson(**payload, actor="agent", origin="agent"),
    )


@tool
def list_playbooks() -> str:
    """List available project-class playbooks (templates for starting an engagement)."""
    return json.dumps(playbooks.list_playbooks())


@tool
def start_engagement_from_playbook(
    playbook_slug: str, engagement_name: str, lead: str = "", start_date: str = ""
) -> str:
    """Instantiate a playbook: creates the engagement, its milestones, tasks,
    and kickoff rituals, and surfaces lessons from past engagements of the
    same class. Prefer this over planning from scratch when a playbook fits.

    Args:
        playbook_slug: One of the slugs from list_playbooks (e.g. prototype, incident, migration).
        engagement_name: Name for the new engagement.
        lead: Who leads it.
        start_date: Start date YYYY-MM-DD (defaults to today).
    """
    payload = {
        "slug": playbook_slug,
        "engagement_name": engagement_name,
        "lead": lead,
        "start_date": start_date,
    }
    return gated_write(
        "playbook",
        "create",
        payload,
        summary=f"instantiate playbook {playbook_slug} -> {engagement_name}",
        direct=lambda: playbooks.instantiate(**payload, actor="agent", origin="agent"),
    )


@tool
def generate_handoff(engagement_id: int) -> str:
    """Generate the rotation handoff package for an engagement (milestone
    status, open tasks, blockers, questions, decisions, lessons) as a markdown
    artifact.

    Args:
        engagement_id: ID of the engagement to hand off.
    """
    return _safe(lambda: handoff.generate_handoff(engagement_id, actor="agent"))


@tool
def search_workspace(query: str) -> str:
    """Full-text search across everything the team has recorded: milestones,
    tasks, questions, decisions, notes, blockers, engagements, lessons.
    Use this to answer "have we seen/decided this before?".

    Args:
        query: What to look for.
    """
    return json.dumps(search.search(query))
