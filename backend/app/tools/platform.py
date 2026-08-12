"""Platform tools: blockers, intake, engagements, playbooks, handoffs, search."""

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
from ..services import (
    blockers,
    engagements,
    handoff,
    intake,
    playbooks,
    projection_policy,
    scope,
    search,
)
from ._gate import gated_write


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
        lambda: blockers.raise_blocker(**payload, actor=agent_identity(), origin="agent"),
    )


@tool
def resolve_blocker(blocker_id: int, resolution: str = "") -> str:
    """Mark a blocker resolved.

    Args:
        blocker_id: ID of the blocker.
        resolution: How it was resolved.
    """
    payload: dict[str, Any] = {"resolution": resolution}
    return gated_write(
        "blocker",
        "update",
        payload,
        lambda: blockers.resolve_blocker(
            blocker_id, **payload, actor=agent_identity(), origin="agent"
        ),
        entity_id=blocker_id,
    )


@tool
def list_blockers(status: str = "", owner: str = "") -> str:
    """List blockers (unresolved by default), most severe first.

    Args:
        status: 'open', 'escalated', 'resolved', or empty for all unresolved.
        owner: Filter to one owner.
    """
    with db.read_transaction():
        rows = blockers.list_blockers(status, owner)
        contexts = blockers.blocker_collection_policy_contexts(rows, scope.NOBODY)
        subject = current_policy_subject()
        engine = current_policy_engine()
        permitted = []
        for row in rows:
            attributes = contexts[int(row["id"])]
            decision = engine.decide(
                PolicyInput(
                    subject,
                    "skein.tool.list_blockers",
                    PolicyResource(
                        "blocker",
                        str(row["id"]),
                        attributes["project_type"],
                        attributes["classification"],
                        attributes,
                    ),
                    "agent_tool",
                    agent=agent_identity(),
                    tool="list_blockers",
                    tool_effect="read",
                    tool_risk="low",
                )
            )
            if decision.effect == PolicyEffect.PERMIT:
                permitted.append(row)
        return json.dumps(permitted)


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
    payload: dict[str, Any] = {
        "title": title,
        "detail": detail,
        "requester": requester,
        "project_class": project_class,
    }
    return gated_write(
        "intake",
        "create",
        payload,
        lambda: intake.submit_request(**payload, actor=agent_identity(), origin="agent"),
    )


@tool
def list_intake_requests(status: str = "") -> str:
    """List intake requests with their triage scores and dispositions.

    Args:
        status: submitted, scored, accepted, deferred, declined, or empty for all.
    """
    with db.read_transaction():
        rows = intake.list_requests(status)
        subject = current_policy_subject()
        engine = current_policy_engine()
        return json.dumps(
            [
                row
                for row in rows
                if engine.decide(
                    PolicyInput(
                        subject,
                        "skein.tool.list_intake_requests",
                        PolicyResource(
                            "intake",
                            str(row["id"]),
                            str(row.get("project_class") or ""),
                            str(row.get("visibility") or ""),
                        ),
                        "agent_tool",
                        agent=agent_identity(),
                        tool="list_intake_requests",
                        tool_effect="read",
                        tool_risk="low",
                    )
                ).effect
                == PolicyEffect.PERMIT
            ]
        )


@tool
def list_engagements(status: str = "") -> str:
    """List engagements (the team's active bodies of work) with allocations.

    Args:
        status: proposed, active, closing, closed, or empty for all.
    """
    with db.read_transaction():
        rows = engagements.list_engagements(status)
        subject = current_policy_subject()
        engine = current_policy_engine()
        return json.dumps(
            [
                row
                for row in rows
                if engine.decide(
                    PolicyInput(
                        subject,
                        "skein.tool.list_engagements",
                        PolicyResource(
                            "engagement",
                            str(row["id"]),
                            str(row.get("project_class") or ""),
                            str(row.get("visibility") or ""),
                        ),
                        "agent_tool",
                        agent=agent_identity(),
                        tool="list_engagements",
                        tool_effect="read",
                        tool_risk="low",
                    )
                ).effect
                == PolicyEffect.PERMIT
            ]
        )


@tool
def team_capacity() -> str:
    """Show total allocation percent per person across active engagements
    (over 100 means overcommitted). Use before accepting new work."""
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.tool.team_capacity",
        "agent_tool",
        scope.NOBODY,
        agent=agent_identity(),
        tool="team_capacity",
    )
    with db.read_transaction():
        if not policy.allows_all_projects():
            return json.dumps({"error": "workplace policy denied this composite read"})
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
        lambda: engagements.record_lesson(**payload, actor=agent_identity(), origin="agent"),
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
    try:
        definition = playbooks.get_playbook(playbook_slug)
        definition_digest = playbooks.definition_digest(definition)
    except ValueError:
        # Keep validation on the shared gate path. It records the same safe
        # failed receipt as the other stock writes.
        definition_digest = ""
    payload: dict[str, Any] = {
        "slug": playbook_slug,
        "engagement_name": engagement_name,
        "lead": lead,
        "start_date": start_date,
        "expected_definition_digest": definition_digest,
    }
    return gated_write(
        "playbook",
        "create",
        payload,
        summary=f"instantiate playbook {playbook_slug} -> {engagement_name}",
        direct=lambda: playbooks.instantiate(
            **payload,
            actor=agent_identity(),
            origin="agent",
        ),
    )


@tool
def generate_handoff(engagement_id: int) -> str:
    """Generate the rotation handoff package for an engagement (milestone
    status, open tasks, blockers, questions, decisions, lessons) as a markdown
    artifact.

    Args:
        engagement_id: ID of the engagement to hand off.
    """
    # writes an artifact (row + file) without the gate: a handoff package is a
    # projection of existing records, not a mutation of them, and the artifact
    # itself is the reviewable output. It still reports what it did.
    from ..agents import receipts

    try:
        result = handoff.generate_handoff(engagement_id, actor=agent_identity())
        receipts.record(
            "wrote",
            "artifact",
            f"handoff package for engagement #{engagement_id}",
            int(result.get("artifact_id") or 0),
        )
        return json.dumps(result)
    except ValueError as exc:
        receipts.record("failed", "artifact", str(exc))
        return json.dumps({"error": str(exc)})


@tool
def search_workspace(query: str) -> str:
    """Full-text search across everything the team has recorded: milestones,
    tasks, questions, decisions, notes, blockers, engagements, lessons.
    Use this to answer "have we seen/decided this before?".

    Args:
        query: What to look for.
    """
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.tool.search_workspace",
        "agent_tool",
        scope.NOBODY,
        agent=agent_identity(),
        tool="search_workspace",
    )
    with db.read_transaction():
        return json.dumps(search.search(query, row_filter=policy.filter_resources))


@tool
def edit_blocker(blocker_id: int, title: str = "", detail: str = "", owner: str = "") -> str:
    """Correct an open blocker's wording or owner ('-' clears detail/owner).
    Resolved blockers are history and refuse edits — resolve_blocker is the
    verb for closing one.

    Args:
        blocker_id: ID of the blocker.
        title: Corrected title.
        detail: Corrected detail ('-' to clear).
        owner: Corrected owner ('-' to clear).
    """
    payload: dict[str, Any] = {
        k: v for k, v in {"title": title, "detail": detail, "owner": owner}.items() if v
    }
    if not payload:
        return json.dumps({"error": "nothing to change — pass at least one field"})
    return gated_write(
        "blocker_edit",
        "update",
        payload,
        lambda: blockers.edit_blocker(
            blocker_id, **payload, actor=agent_identity(), origin="agent"
        ),
        entity_id=blocker_id,
        summary=f"edit blocker #{blocker_id}",
    )


@tool
def edit_intake_request(request_id: int, title: str = "", detail: str = "") -> str:
    """Fix an intake request's wording while it is still un-triaged
    (submitted/scored). Dispositioned requests are history and refuse edits.

    Args:
        request_id: ID of the request.
        title: Corrected title.
        detail: Corrected detail ('-' to clear).
    """
    payload: dict[str, Any] = {k: v for k, v in {"title": title, "detail": detail}.items() if v}
    if not payload:
        return json.dumps({"error": "nothing to change — pass title and/or detail"})
    return gated_write(
        "intake_edit",
        "update",
        payload,
        lambda: intake.edit_request(request_id, **payload, actor=agent_identity(), origin="agent"),
        entity_id=request_id,
        summary=f"edit intake #{request_id}",
    )


@tool
def update_engagement(
    engagement_id: int,
    status: str = "",
    name: str = "",
    summary: str = "",
    lead: str = "",
    conclusion: str = "",
    outcome: str = "",
    timebox_end: str = "",
    kill_criteria: str = "",
) -> str:
    """Update an engagement — status, rename (propagates to milestone labels),
    lead, summary, or close it. Closing requires a conclusion (achieved /
    partial / missed / invalidated / unmeasured / stopped) in the SAME call.

    Args:
        engagement_id: ID of the engagement.
        status: New status (proposed/active/closing/closed).
        name: New name — must be unique.
        summary: Updated summary.
        lead: New lead.
        conclusion: Honest outcome, required when closing.
        outcome: Outcome statement.
        timebox_end: New timebox end (YYYY-MM-DD) for experiments.
        kill_criteria: Updated kill criteria for experiments.
    """
    payload: dict[str, Any] = {
        k: v
        for k, v in {
            "status": status,
            "name": name,
            "summary": summary,
            "lead": lead,
            "conclusion": conclusion,
            "outcome": outcome,
            "timebox_end": timebox_end,
            "kill_criteria": kill_criteria,
        }.items()
        if v
    }
    if not payload:
        return json.dumps({"error": "nothing to change — pass at least one field"})
    if payload.get("status") == "closed" and not payload.get("conclusion"):
        return json.dumps(
            {
                "error": "closing needs a conclusion in the same call — one of"
                " achieved/partial/missed/invalidated/unmeasured/stopped"
            }
        )
    return gated_write(
        "engagement",
        "update",
        payload,
        lambda: engagements.update_engagement(
            engagement_id, **payload, actor=agent_identity(), origin="agent"
        ),
        entity_id=engagement_id,
        summary=f"update engagement #{engagement_id}",
    )
