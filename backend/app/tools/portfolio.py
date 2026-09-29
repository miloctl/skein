"""Portfolio tools: portfolio reads, promises, delegation, decision chains,
context pack, agent inbox."""

import json
from typing import Any

from strands import tool

from .. import db, ratelimit
from ..agents import receipts
from ..agents.identity import (
    agent_identity,
    requester_identity,
    requester_viewer,
    strong_requester,
    workspace_only_tools,
)
from ..extensions.policy import (
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    current_policy_engine,
    current_policy_subject,
)
from ..services import (
    absences,
    briefing,
    collab,
    comments,
    context_pack,
    delegation,
    insights,
    policy_context,
    portfolio,
    projection_policy,
    promises,
    scope,
    users,
)
from ._gate import gated_write


@tool
def get_portfolio_health() -> str:
    """Engagement health (red/yellow/green) with the receipts behind each
    verdict: overdue milestones, open/escalated blockers, stale work."""
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.tool.get_portfolio_health",
        "agent_tool",
        scope.NOBODY,
        agent=agent_identity(),
        tool="get_portfolio_health",
    )
    with db.read_transaction():
        rows = portfolio.engagement_health(resource_filter=policy.permits)
        return json.dumps(policy.filter_rows("engagement", rows))


@tool
def get_flow_metrics() -> str:
    """Team flow metrics from real timestamps: cycle time, weekly throughput,
    total work in progress, and stale in-progress tasks. Use team_capacity
    when the question is who has room."""
    # name_people=False: an agent's reply is text somebody pastes elsewhere,
    # and this read judges the PAST — which the anti-surveillance rule allows
    # only as a team aggregate (docs/FEATURES.md). The names stay on
    # /portfolio, which is a planning surface with a viewer and an audience.
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.tool.get_flow_metrics",
        "agent_tool",
        scope.NOBODY,
        agent=agent_identity(),
        tool="get_flow_metrics",
    )
    with db.read_transaction():
        if not policy.allows_all_projects() or not policy.allows_unclassified():
            return json.dumps({"error": "workplace policy denied this composite read"})
        return json.dumps(portfolio.flow_metrics(name_people=False))


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
        policy = projection_policy.ProjectionPolicy(
            current_policy_engine(),
            current_policy_subject(),
            "skein.tool.what_if_staffing",
            "agent_tool",
            scope.NOBODY,
            agent=agent_identity(),
            tool="what_if_staffing",
        )
        with db.read_transaction():
            if not policy.allows_all_projects() or not policy.allows_unclassified():
                return json.dumps({"error": "workplace policy denied this composite read"})
            return json.dumps(portfolio.what_if(request_id, names, percent))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@tool
def add_promise(
    promise: str, to_whom: str = "", due_date: str = "", engagement_id: int = 0, event_id: int = 0
) -> str:
    """Record an external promise (one made to someone outside the
    team) so the exec readout tracks it.

    Args:
        promise: What was promised.
        to_whom: Who it was promised to.
        due_date: When it's due (YYYY-MM-DD).
        engagement_id: Related engagement, or 0.
        event_id: The meeting this came out of (an ID from list_events), or 0.
    """
    payload: dict[str, Any] = {
        "promise": promise,
        "to_whom": to_whom,
        "due_date": due_date,
        "engagement_id": engagement_id,
        **({"event_id": event_id} if event_id else {}),
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
    with db.read_transaction():
        rows = promises.list_promises(status)
        contexts = policy_context.engagement_linked_collection_contexts(
            "promise", rows, scope.NOBODY
        )
        subject = current_policy_subject()
        engine = current_policy_engine()
        return json.dumps(
            [
                row
                for row in rows
                if engine.decide(
                    PolicyInput(
                        subject,
                        "skein.tool.list_promises",
                        PolicyResource(
                            "promise",
                            str(row["id"]),
                            contexts[int(row["id"])]["project_type"],
                            contexts[int(row["id"])]["classification"],
                            contexts[int(row["id"])],
                        ),
                        "agent_tool",
                        agent=agent_identity(),
                        tool="list_promises",
                        tool_effect="read",
                        tool_risk="low",
                    )
                ).effect
                == PolicyEffect.PERMIT
            ]
        )


@tool
def delegate_task(
    task_id: int,
    agent: str,
    sponsor: str,
    acceptance_criteria: str = "",
    check_in_at: str = "",
) -> str:
    """Delegate a task to an AI agent with a human sponsor who stays
    accountable for it.

    Args:
        task_id: The task to delegate.
        agent: Agent identity that will do the work.
        sponsor: Human teammate accountable for the outcome.
        acceptance_criteria: What done means — the sponsor's verdict reads this.
        check_in_at: Date (YYYY-MM-DD) by which the agent must have a progress note.
    """
    payload: dict[str, Any] = {
        "task_id": task_id,
        "agent": agent,
        "sponsor": sponsor,
        "acceptance_criteria": acceptance_criteria,
        "check_in_at": check_in_at,
    }
    return gated_write(
        "delegation",
        "create",
        payload,
        summary=f"delegate task #{task_id} to {agent}",
        direct=lambda: delegation.delegate_task(
            **payload,
            actor=agent_identity(),
            origin="agent",
            # a NEW agent identity needs the requester's proven credential,
            # the bar POST /api/tasks/{id}/delegate applies
            mint_authorized=bool(strong_requester()),
        ),
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
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.tool.get_context_pack",
        "agent_tool",
        scope.NOBODY,
        agent=agent_identity(),
        tool="get_context_pack",
    )
    if not engagement_id:
        # before the snapshot: the first read publishes (context_pack.ensure_published)
        context_pack.ensure_published(actor=agent_identity(), resource_filter=policy.permits)
    with db.read_transaction():
        if engagement_id:
            attributes = policy_context.existing_scoped("engagement", engagement_id, scope.NOBODY)
            if not attributes or not policy.permits("engagement", engagement_id, attributes):
                return json.dumps({"error": f"no engagement #{engagement_id}"})
            return json.dumps(
                {
                    "engagement": engagement_id,
                    "content": context_pack.build_engagement_pack(
                        engagement_id,
                        resource_filter=policy.permits,
                    ),
                }
            )
        return json.dumps(
            context_pack.get_pack(
                actor=agent_identity(),
                resource_filter=policy.permits,
            )
        )


@tool
# Takes no name, and must not gain one. delegation.agent_inbox answers for
# whatever roster row it is handed — human or agent — with assigned questions,
# rejected proposals INCLUDING reviewer notes, and 20 unread notification
# bodies. As a model-controlled argument, "check the agent inbox for mira" was
# the whole exploit. The MCP twin lost the same parameter for the same reason
# (app/mcp_server.py::get_my_day). Pinned by
# tests/test_privacy.py::test_the_agent_inbox_tool_takes_no_name.
def my_agent_inbox() -> str:
    """Your own ambient inbox: delegated tasks, questions assigned to you,
    rejected proposals (with reviewer notes), notifications."""
    try:
        # the REQUESTER's viewer, not None: None means "the agent is the
        # caller" and leaves the inbox unfiltered, which is right for MCP and
        # the scheduler. `/as <persona>` makes that false — a human takes the
        # persona's identity, every shipped persona holds this tool, and the
        # REST twin refuses the same read. Unset outside a chat turn, so the
        # autonomous path is unchanged.
        rv = requester_viewer()
        viewer = rv if isinstance(rv, scope.Viewer) else scope.NOBODY
        service_viewer = rv if isinstance(rv, scope.Viewer) else None
        policy = projection_policy.ProjectionPolicy(
            current_policy_engine(),
            current_policy_subject(),
            "skein.tool.my_agent_inbox",
            "agent_tool",
            viewer,
            agent=agent_identity(),
            tool="my_agent_inbox",
        )
        with db.read_transaction():
            return json.dumps(
                delegation.agent_inbox(
                    agent_identity(),
                    service_viewer,
                    task_filter=lambda task_id, attributes: policy.permits(
                        "task", task_id, attributes
                    ),
                    resource_filter=policy.permits,
                    allow_unclassified=policy.allows_unclassified(),
                )
            )
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


def _delegation_reach(task_id: int) -> str:
    """The party door (delegation.list_worklog, claim_task, report_progress,
    submit_completion) opens for the delegated agent whatever the tier says.
    In a chat turn a HUMAN drives that agent: `/as <persona>` hands anyone the
    persona's identity, so the door must also be the requester's read
    (work.get_task through existing_scoped), which the gate and the REST twin
    already apply. No requester (the unattended runner, the scheduler) keeps
    the party path. Returns the refusal text, or "" when the read holds."""
    rv = requester_viewer()
    if not isinstance(rv, scope.Viewer):
        return ""
    if policy_context.existing_scoped("task", task_id, rv):
        return ""
    return scope.missing_text("tasks", task_id)


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
    if refusal := _delegation_reach(task_id):
        return json.dumps({"error": refusal})
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
    if refusal := _delegation_reach(task_id):
        return json.dumps({"error": refusal})
    try:
        result = delegation.report_progress(task_id, note, actor=agent_identity())
        receipts.record("wrote", "worklog", f"progress on task #{task_id}: {note[:80]}", task_id)
        return json.dumps(result)
    except ValueError as exc:
        receipts.record("failed", "worklog", str(exc))
        return json.dumps({"error": str(exc)})


@tool
def read_worklog(task_id: int, limit: int = 20) -> str:
    """Read the progress notes already logged on a delegated task — yours and
    your sponsor's. Read this BEFORE continuing work you started earlier: it
    is where you recorded what you found, what you decided, and what you were
    waiting on.

    Args:
        task_id: ID of the task.
        limit: How many of the most recent notes to return (default 20).
    """
    # The continuity record for multi-day work. Without a reader, an agent
    # resuming on day 3 restarted from the task title: the chat session that
    # held the rest is gone (the conversation manager drops the oldest
    # messages, and pin_first is inert across turns —
    # agents/team_agent.py::_conversation_manager).
    #
    # actor=, so the delegation itself is the door: an agent holds no crew
    # membership, so on a crew task the tier filter alone would refuse the
    # worklog this agent is WRITING (services/delegation.py::list_worklog).
    # The viewer stays NOBODY — the workspace tier — for every other task. In
    # a human-driven turn the requester's own read comes first
    # (_delegation_reach), or a persona hands its party rights to anyone.
    if refusal := _delegation_reach(task_id):
        return json.dumps({"error": refusal})
    try:
        notes = delegation.list_worklog(task_id, limit, actor=agent_identity())
        # a read, so no receipt: receipts record WRITES, and the gate-coverage
        # suite asserts a receipt only where the DB was mutated
        return json.dumps({"task_id": task_id, "worklog": notes})
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


def _thread_reach(kind: str, pid: int) -> str:
    """The requester's read of a thread's parent, the check every agent
    comment tool runs first. A task goes through _delegation_reach, so its
    delegate keeps the party door when no person asked. Returns the refusal
    text, or "" when the read holds."""
    if kind == "task":
        return _delegation_reach(pid)
    rv = requester_viewer()
    if not isinstance(rv, scope.Viewer):
        return ""
    if policy_context.existing_scoped(kind, pid, rv):
        return ""
    return scope.missing_text(f"{kind}s", pid)


@tool
def read_comments(
    task_id: int = 0, decision_id: int = 0, blocker_id: int = 0, limit: int = 20
) -> str:
    """Read the comment thread on one task, decision or blocker, oldest
    first. On a task delegated to you, read it before you continue the work:
    your sponsor steers it there. Name exactly one of the three ids.

    Args:
        task_id: ID of the task, or 0.
        decision_id: ID of the decision, or 0.
        blocker_id: ID of the blocker, or 0.
        limit: How many comments to return (default 20, at most 200).
    """
    try:
        kind, pid = comments.parent_of(task_id, decision_id, blocker_id)
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    if refusal := _thread_reach(kind, pid):
        return json.dumps({"error": refusal})
    try:
        # a read, so no receipt (tests/test_gate_coverage.py asserts receipts
        # only where the database changed). actor=: the delegate's door onto a
        # crew task it holds (comments.list_comments)
        rows = comments.list_comments(
            scope.NOBODY,
            task_id=task_id,
            decision_id=decision_id,
            blocker_id=blocker_id,
            actor=agent_identity(),
            limit=limit,
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    keep = ("id", "created_by", "origin", "body", "created_at", "edited_at", "deleted_at")
    return json.dumps({kind: pid, "comments": [{key: row[key] for key in keep} for row in rows]})


@tool
def post_comment(body: str, task_id: int = 0, decision_id: int = 0, blocker_id: int = 0) -> str:
    """Post a comment on one task, decision or blocker. On an open task
    delegated to you it posts at once: use it to answer your sponsor. Answer
    once, and do not answer a comment that asks you nothing. On any other
    record a person reviews it first. Name exactly one of the three ids.

    Args:
        body: The comment, at most 4000 characters. Write @name to notify a person.
        task_id: ID of the task, or 0.
        decision_id: ID of the decision, or 0.
        blocker_id: ID of the blocker, or 0.
    """
    agent = agent_identity()
    try:
        kind, pid = comments.parent_of(task_id, decision_id, blocker_id)
        comments.refuse_forbidden(agent, kind)
    except ValueError as exc:
        receipts.record("refused", "comment", str(exc))
        return json.dumps({"error": str(exc)})
    if refusal := _thread_reach(kind, pid):
        return json.dumps({"error": refusal})
    if kind == "task" and comments.open_delegate(pid) == agent:
        # the delegate's direct path, as report_progress: the gate's own rate
        # line, keyed on the person who asked, else the agent itself
        try:
            ratelimit.check("write", requester_identity() or agent)
            result = comments.add_comment(
                body, task_id=pid, actor=agent, origin="agent", as_delegate=True
            )
        except ValueError as exc:
            receipts.record("failed", "comment", str(exc))
            return json.dumps({"error": str(exc)})
        receipts.record("wrote", "comment", f"comment on task #{pid}", int(result["id"]))
        return json.dumps(result)
    rv = requester_viewer()
    if not (isinstance(rv, scope.Viewer) and rv.name):
        # unattended: an agent speaks only where it holds the work (owner
        # decision, docs/intent/task-threads.md D4)
        detail = (
            f"{kind} #{pid} is not an open task delegated to you. With no person asking,"
            " comment only on a task delegated to you."
        )
        receipts.record("refused", "comment", detail)
        return json.dumps({"error": detail})
    context = policy_context.existing_scoped(kind, pid, rv)
    if str(context.get("classification") or "") == scope.PRIVATE:
        # the approval applies as the agent, and scope.assert_editable refuses
        # a machine on a private row, so the proposal could never apply
        detail = "An agent cannot comment on a private record. Write the comment yourself."
        receipts.record("refused", "comment", detail)
        return json.dumps({"error": detail})
    payload = {"body": body, f"{kind}_id": pid}
    return gated_write(
        "comment",
        "create",
        payload,
        lambda: comments.add_comment(
            body,
            task_id=task_id,
            decision_id=decision_id,
            blocker_id=blocker_id,
            actor=agent,
            origin="agent",
        ),
        summary=f"comment on {kind} #{pid}",
    )


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

    if refusal := _delegation_reach(task_id):
        return json.dumps({"error": refusal})
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
    person: str,
    starts_on: str,
    ends_on: str,
    kind: str = "pto",
    note: str = "",
    team_sees: str = "",
) -> str:
    """Record time away (pto / oncall / focus). Capacity, the weekly plan, and
    staffing what-ifs respect it only when the team sees its dates.

    Args:
        person: Who is away.
        starts_on: First day (YYYY-MM-DD).
        ends_on: Last day (YYYY-MM-DD).
        kind: pto (zeroes planning), oncall, or focus (advisory).
        note: Optional context.
        team_sees: "nothing" (only the person away sees it, and planning
            ignores it), "dates" (planning counts it, the kind and note stay
            with the person away), or "details" (everyone on the roster sees
            it). Leave it empty to use the narrowest choice. For a teammate's
            time away "nothing" is refused: the window is theirs, and the
            team must be able to plan around it.
    """
    if team_sees not in ("", "nothing", "dates", "details"):
        return json.dumps({"error": 'team_sees must be "nothing", "dates" or "details"'})
    # Only the person away can keep a window from the team, and only with a
    # strong identity: a weak viewer reads no private row, and a private
    # window about somebody else is refused at apply
    # (scope.assert_readable_by), where the proposal would wait forever.
    # A shared chat writes workspace rows only (tools/_gate.py refuses the
    # rest), so its strong member files a window the team sees.
    strong = "" if workspace_only_tools() else strong_requester()
    own = bool(strong) and users.fold(person) == users.fold(strong)
    # a strong requester files a teammate's window as theirs with the dates
    # shared (absences.add_absence); a weak one reads no private row, so
    # the roster is all it can file
    team_sees = team_sees or ("nothing" if own else ("dates" if strong else "details"))
    if team_sees != "details" and workspace_only_tools():
        return json.dumps(
            {
                "error": "In a shared chat, time away is recorded for the whole team."
                ' Use team_sees "details", or record it in your own chat.'
            }
        )
    if team_sees != "details" and not strong:
        return json.dumps(
            {
                "error": "Only a requester with a key or a sign-in can keep time away"
                ' from the team. Use team_sees "details" for this window.'
            }
        )
    if team_sees == "nothing" and not own:
        return json.dumps(
            {
                "error": "Only the person away, with a key or a sign-in, can keep time away"
                ' from planning. Use team_sees "dates" or "details" for this window.'
            }
        )
    payload: dict[str, Any] = {
        "person": person,
        "starts_on": starts_on,
        "ends_on": ends_on,
        "kind": kind,
        "note": note,
        "visibility": scope.WORKSPACE if team_sees == "details" else scope.PRIVATE,
        "dates_shared": team_sees != "nothing",
    }
    return gated_write(
        "absence",
        "create",
        payload,
        lambda: absences.add_absence(
            **payload,
            actor=agent_identity(),
            origin="agent",
            requester=requester_identity(),
        ),
        # the kind and dates only where the team sees them
        summary=(
            f"absence: {person} {kind} {starts_on}..{ends_on}"
            if team_sees == "details"
            else f"time away for {person}"
        ),
    )


@tool
def list_absences(person: str = "") -> str:
    """Current and upcoming time away for the team (or one person).

    Args:
        person: Optional filter.
    """
    return json.dumps(absences.list_absences(person))


@tool
def get_findings(weeks: int = 2, limit: int = 10) -> str:
    """What the findings engine noticed: deterministic rules over blockers,
    stale work, promises, the review queue, intake, decisions, spend and jobs.

    Read this before you answer "what needs attention", "what is at risk" or
    "what should worry me" about the TEAM. Every finding carries its receipt
    (the row ids and numbers it fired on) and its severity. Cite the receipt
    and the severity. Do not restate the message.

    Args:
        weeks: How far back to look, in weeks (1-52).
        limit: The maximum number of findings to return (1-50).
    """
    # A read, so no gate: it writes nothing and takes no authority level.
    # Team aggregates only — the rules never key on a person, which is what
    # the anti-surveillance rule requires of anything judging the PAST.
    weeks = max(1, min(int(weeks), 52))
    limit = max(1, min(int(limit), 50))
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.tool.get_findings",
        "agent_tool",
        scope.NOBODY,
        agent=agent_identity(),
        tool="get_findings",
    )
    with db.read_transaction():
        if not policy.allows_all_projects() or not policy.allows_unclassified():
            return json.dumps({"error": "workplace policy denied this composite read"})
        return json.dumps(insights.list_findings(weeks=weeks, limit=limit))


@tool
def get_attention() -> str:
    """What is waiting on the person you are talking to, grouped by the
    judgment each item asks for: decide, unblock, commit, review, notice.

    Read this before you answer "what should I do today" or "what is on me".
    Every item carries a `reason` field. Say that reason. The reason is what
    makes the item actionable.
    """
    # The REQUESTER's day, never the agent's own, and filtered by their
    # viewer: my_day takes a Viewer because these lists are addressed to a
    # person by name, and a name is self-asserted in trusted-header mode
    # (services/briefing.py). No argument names a person, for the same reason
    # my_agent_inbox takes none — tests/test_privacy.py pins that shape.
    rv = requester_viewer()
    # isinstance, not a truth test: the contextvar is typed `object | None`
    # because identity.py must not import services (the same narrow
    # my_agent_inbox makes, a few tools up).
    if not isinstance(rv, scope.Viewer) or not rv.name:
        return json.dumps({"error": "this turn has no requester — ask the person what is on them"})
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.tool.get_attention",
        "agent_tool",
        rv,
        agent=agent_identity(),
        tool="get_attention",
    )
    with db.read_transaction():
        return json.dumps(
            briefing.my_day(
                rv.name,
                rv,
                policy.filter_rows,
                policy.filter_resources,
                policy.allows_unclassified(),
                policy.permits,
            )["attention"]
        )
