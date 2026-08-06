"""Portfolio tools: portfolio reads, promises, delegation, decision chains,
context pack, agent inbox."""

import json
from typing import Any

from strands import tool

from ..agents import receipts
from ..agents.identity import agent_identity
from ..services import absences, collab, context_pack, delegation, portfolio, promises
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
def add_promise(promise: str, to_whom: str = "", due_date: str = "", engagement_id: int = 0) -> str:
    """Record an external promise (one made to someone outside the
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
        "promise",
        "create",
        payload,
        lambda: promises.add_promise(**payload, actor=agent_identity(), origin="agent"),
    )


@tool
def list_promises(status: str = "") -> str:
    """List external promises the team has made.

    Args:
        status: open, kept, missed, withdrawn, or empty for all.
    """
    return json.dumps(promises.list_promises(status))


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
        direct=lambda: delegation.delegate_task(**payload, actor=agent_identity(), origin="agent"),
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
            decision_id, **payload, actor=agent_identity(), origin="agent"
        ),
    )


@tool
def get_context_pack(engagement_id: int = 0) -> str:
    """The team context pack (org-brain): active decisions, engagement state,
    lessons, conventions. Load this before working on anything team-related.

    Args:
        engagement_id: Pass an engagement id to get the SCOPED pack for that
            engagement only (outcome, milestones, open tasks, blockers,
            lessons) — cheaper and more focused for delegated work. 0 = the
            full team pack.
    """
    if engagement_id:
        return json.dumps(
            {
                "engagement": engagement_id,
                "content": context_pack.build_engagement_pack(engagement_id),
            }
        )
    return json.dumps(context_pack.get_pack(actor=agent_identity()))


@tool
# Takes no name, and must not gain one. delegation.agent_inbox answers for
# whatever roster row it is handed — human or agent — with assigned questions,
# rejected proposals INCLUDING reviewer notes, and 20 unread notification
# bodies. As a model-controlled argument, "check the agent inbox for mira" was
# the whole exploit. The MCP twin lost the same parameter for the same reason
# (app/mcp_server.py::get_my_day). Pinned by tests/test_privacy.py.
def my_agent_inbox() -> str:
    """Your own ambient inbox: delegated tasks, questions assigned to you,
    rejected proposals (with reviewer notes), notifications."""
    try:
        return json.dumps(delegation.agent_inbox(agent_identity()))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@tool
def edit_promise(promise_id: int, promise: str = "", due_date: str = "", to_whom: str = "") -> str:
    """Correct the wording, due date, or recipient of an OPEN promise
    ('-' clears due_date/to_whom). Settled promises are history and
    refuse edits.

    Args:
        promise_id: ID of the promise.
        promise: Corrected promise text.
        due_date: Corrected due date (YYYY-MM-DD, '-' to clear).
        to_whom: Corrected recipient ('-' to clear).
    """
    payload = {
        k: v for k, v in {"promise": promise, "due_date": due_date, "to_whom": to_whom}.items() if v
    }
    return gated_write(
        "promise_edit",
        "update",
        payload,
        lambda: promises.edit_promise(
            promise_id, **payload, actor=agent_identity(), origin="agent"
        ),
        entity_id=promise_id,
        summary=f"edit promise #{promise_id}",
    )


@tool
def mark_promise(promise_id: int, status: str) -> str:
    """Settle an OPEN promise: kept, missed, or withdrawn. Already-settled
    promises are history and refuse changes.

    Args:
        promise_id: ID of the promise.
        status: One of kept / missed / withdrawn.
    """
    if status not in ("kept", "missed", "withdrawn"):
        return json.dumps({"error": "status must be kept, missed, or withdrawn"})
    payload = {"status": status}
    return gated_write(
        "promise_settle",
        "update",
        payload,
        lambda: promises.update_promise(
            promise_id, **payload, actor=agent_identity(), origin="agent"
        ),
        entity_id=promise_id,
        summary=f"mark promise #{promise_id} {status}",
    )


@tool
def claim_delegated_task(task_id: int) -> str:
    """Pick up a task delegated to you: flips it to in_progress and tells
    your sponsor you started. Use before doing the work.

    Args:
        task_id: ID of the task delegated to you.
    """
    # the delegation loop bypasses the generic gate on purpose (sponsor-bound
    # verdicts, not the authority matrix) — so it must record its own
    # receipts, or the UI cannot state that the write happened
    try:
        result = delegation.claim_task(task_id, actor=agent_identity())
        receipts.record("wrote", "task", f"claimed delegated task #{task_id}", task_id)
        return json.dumps(result)
    except ValueError as exc:
        receipts.record("failed", "task", str(exc))
        return json.dumps({"error": str(exc)})


@tool
def report_progress(task_id: int, note: str) -> str:
    """Log a progress note on a delegated task — your sponsor reads the
    worklog before accepting. Report as you go, not only at the end.

    Args:
        task_id: ID of the task.
        note: What you did / found / decided since the last note.
    """
    try:
        result = delegation.report_progress(task_id, note, actor=agent_identity())
        receipts.record("wrote", "worklog", f"progress on task #{task_id}: {note[:80]}", task_id)
        return json.dumps(result)
    except ValueError as exc:
        receipts.record("failed", "worklog", str(exc))
        return json.dumps({"error": str(exc)})


@tool
def submit_for_acceptance(task_id: int, summary: str) -> str:
    """Submit a delegated task as finished. This ALWAYS files a proposal —
    your sponsor's verdict marks it done (and every verdict builds or costs
    your trust score). Never claim the task is done after calling this;
    say it awaits acceptance.

    Args:
        task_id: ID of the task delegated to you.
        summary: What was delivered — the sponsor reads exactly this.
    """
    from ..agents.identity import requester_identity

    try:
        result = delegation.submit_completion(
            task_id, summary, actor=agent_identity(), requested_by=requester_identity()
        )
        # a filed proposal with no receipt reads as nothing having happened —
        # the exact silence the turn guard exists to catch
        receipts.record(
            "queued",
            "task_completion",
            f"task #{task_id} awaits the sponsor's acceptance",
            int(result.get("proposal_id") or 0),
        )
        return json.dumps(result)
    except ValueError as exc:
        receipts.record("failed", "task_completion", str(exc))
        return json.dumps({"error": str(exc)})


@tool
def add_absence(
    person: str, starts_on: str, ends_on: str, kind: str = "pto", note: str = ""
) -> str:
    """Record time away (pto / oncall / focus) so capacity, the weekly plan,
    and staffing what-ifs respect it.

    Args:
        person: Who is away.
        starts_on: First day (YYYY-MM-DD).
        ends_on: Last day (YYYY-MM-DD).
        kind: pto (zeroes planning), oncall, or focus (advisory).
        note: Optional context.
    """
    payload = {
        "person": person,
        "starts_on": starts_on,
        "ends_on": ends_on,
        "kind": kind,
        "note": note,
    }
    return gated_write(
        "absence",
        "create",
        payload,
        lambda: absences.add_absence(**payload, actor=agent_identity(), origin="agent"),
        summary=f"absence: {person} {kind} {starts_on}..{ends_on}",
    )


@tool
def list_absences(person: str = "") -> str:
    """Current and upcoming time away for the team (or one person).

    Args:
        person: Optional filter.
    """
    return json.dumps(absences.list_absences(person))
