"""Skein as an MCP server — so the team's OTHER AI agents (Claude
Code sessions, custom agents) can read/write the platform natively.

Runs in-process against the same database (no HTTP hop):

    cd backend && SKEIN_MCP_USER=mario-mcp .venv/bin/python -m app.mcp_server

Claude Code registration:

    claude mcp add skein -- env SKEIN_MCP_USER=you-mcp \
        /path/to/backend/.venv/bin/python -m app.mcp_server

Writes are attributed to SKEIN_MCP_USER (shown with origin=agent).
Gating: EVERY writer here goes through the same authority gate as the
chat-agent tools — with review mode on they queue in /review and build trust
scores. capture gates on the entity its text classifies to, so prefixing a
message is not a way around the review inbox. The delegation trio (claim,
report_progress, submit_for_acceptance) is direct by design — working your own
delegation is not a proposal — and each one honors the forbidden kill switch.
"""

import json
import os
from collections.abc import Sequence
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import db, ratelimit
from .extensions import PolicySubject, SkeinModule
from .extensions.core import core_module
from .extensions.policy import (
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    current_policy_engine,
    current_policy_subject,
    reset_policy_engine,
    reset_policy_subject,
    set_policy_engine,
    set_policy_subject,
)
from .extensions.registry import ExtensionRegistry
from .services import blockers as blockers_svc
from .services import briefing as briefing_svc
from .services import capture as capture_svc
from .services import (
    collab,
    context_pack,
    delegation,
    memory,
    portfolio,
    projection_policy,
    scope,
    search,
    work,
)
from .services import policy_context as domain_policy_context
from .services.adoption import record_use
from .tools._gate import gated_write

ACTOR = os.getenv("SKEIN_MCP_USER", "mcp-agent")

mcp = FastMCP("skein")


def _policy_refusal(
    action: str,
    resource_type: str,
    resource_id: int | str = "",
    *,
    effect: str = "read",
    risk: str = "low",
) -> str:
    """Return a JSON refusal, or an empty string when policy permits."""
    attributes: dict[str, Any] = {}
    if resource_type == "task" and resource_id:
        # Policy context is non-content metadata. It must not use the caller's
        # content-visibility filter: a delegated agent can legitimately act on
        # a crew task without being a crew member, and policy still needs the
        # task's authoritative classification and project type.
        attributes = domain_policy_context.existing("task", int(resource_id))
    elif resource_id:
        try:
            attributes = domain_policy_context.existing_scoped(
                resource_type,
                int(resource_id),
                scope.NOBODY,
            )
        except (TypeError, ValueError):
            attributes = {}
    decision = current_policy_engine().decide(
        PolicyInput(
            current_policy_subject(),
            action,
            PolicyResource(
                resource_type,
                str(resource_id),
                str(attributes.get("project_type") or ""),
                str(attributes.get("classification") or ""),
                attributes,
            ),
            "mcp",
            agent=ACTOR,
            tool=action,
            tool_effect=effect,
            tool_risk=risk,
        )
    )
    if decision.effect == PolicyEffect.PERMIT:
        return ""
    return json.dumps(
        {
            "error": (
                "workplace policy requires review"
                if decision.effect == PolicyEffect.REVIEW
                else "workplace policy denied this operation"
            ),
            "policy_effect": decision.effect.value,
        }
    )


def _policy_permits(
    action: str,
    resource_type: str,
    resource_id: int,
    attributes: dict[str, str],
) -> bool:
    decision = current_policy_engine().decide(
        PolicyInput(
            current_policy_subject(),
            action,
            PolicyResource(
                resource_type,
                str(resource_id),
                str(attributes.get("project_type") or ""),
                str(attributes.get("classification") or ""),
                attributes,
            ),
            "mcp",
            agent=ACTOR,
            tool=action,
            tool_effect="read",
            tool_risk="low",
        )
    )
    return decision.effect == PolicyEffect.PERMIT


@mcp.tool()
# Takes no person parameter, and must not gain one: briefing.my_day answers
# for whatever name it is handed — assigned questions, owned blockers, tasks,
# and the BODIES of unread notifications. One model-controlled argument
# enumerated any teammate's inbox over a surface whose whole identity is an
# environment variable. Pinned by tests/test_privacy.py.
def get_my_day() -> str:
    """The briefing for this agent identity: what needs attention, tasks,
    blockers, today's events."""
    record_use(ACTOR, "mcp")
    if refusal := _policy_refusal("skein.mcp.briefing.read", "briefing"):
        return refusal
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.mcp.briefing.read",
        "mcp",
        scope.NOBODY,
        agent=ACTOR,
        tool="skein.mcp.briefing.read",
    )
    with db.read_transaction():
        row_filter = None if policy.allows_all_projects() else policy.filter_rows
        return json.dumps(briefing_svc.my_day(ACTOR, row_filter=row_filter))


@mcp.tool()
def capture(text: str) -> str:
    """Quick-capture freeform text; auto-routed to task / question / note /
    decision / blocker / promise (e.g. 'todo: ship the API', 'blocked on vendor')."""
    record_use(ACTOR, "mcp")
    ratelimit.check("capture", ACTOR)
    # Route through the SAME gate every other MCP writer uses, on the entity
    # the text classifies to. Checking only `forbidden` honored the kill
    # switch but skipped the DEFAULT level: an agent at `review` had its
    # create_task queued and its `todo: …` capture written straight through,
    # so prefixing the text was a one-word way around the review inbox for
    # seven entity types.
    # capture.plan gives the entity AND the handler's own kwargs, so a queued
    # proposal is applicable. A generic {"text": ...} was applicable by
    # nothing: every proposal failed at apply and reset to pending.
    # fb: BEFORE plan(). capture() has this guard, but the proposal path never
    # calls capture() — review.approve_change applies the payload straight
    # against the registry. Without this, a private feedback line became a
    # note proposal sitting in the TEAM-VISIBLE review queue, and approving it
    # wrote (and FTS-indexed) a public note. chat.py and session_log.py both
    # refuse at the surface; this was the one writer that did not.
    if capture_svc.is_private_feedback(text):
        return json.dumps(
            {
                "error": "feedback notes are private and never route through an agent."
                " Use the fb: prefix in the web palette with your personal API key."
            }
        )
    kind, entity, payload = capture_svc.plan(text, actor=ACTOR)
    return gated_write(
        entity,
        "create",
        payload,
        lambda: capture_svc.capture(text, actor=ACTOR, origin="agent"),
        summary=f"capture ({kind}): {text.strip()[:80]}",
        actor=ACTOR,
    )


@mcp.tool()
def create_task(
    title: str, description: str = "", assignee: str = "", priority: str = "medium"
) -> str:
    """Create a task in the team tracker. priority: low|medium|high|urgent.
    With review mode on, queues for human approval unless this agent has
    autonomous authority for tasks."""
    record_use(ACTOR, "mcp")
    payload: dict[str, Any] = {
        "title": title,
        "description": description,
        "assignee": assignee,
        "priority": priority,
    }
    return gated_write(
        "task",
        "create",
        payload,
        lambda: work.create_task(**payload, actor=ACTOR, origin="agent"),
        actor=ACTOR,
    )


@mcp.tool()
def complete_task(task_id: int) -> str:
    """Mark a task done (queued for review unless this agent is autonomous).
    For a task DELEGATED to you, use submit_for_acceptance instead — the
    sponsor's verdict is the only thing that closes delegated work."""
    record_use(ACTOR, "mcp")
    return gated_write(
        "task",
        "update",
        {"status": "done"},
        lambda: work.update_task(task_id, status="done", actor=ACTOR, origin="agent"),
        entity_id=task_id,
        actor=ACTOR,
    )


@mcp.tool()
def claim_delegated_task(task_id: int) -> str:
    """Claim a task delegated to you: todo -> in_progress, sponsor notified.
    Start here before working a delegated task from my_inbox."""
    record_use(ACTOR, "mcp")
    with db.transaction():
        if refusal := _policy_refusal(
            "skein.mcp.delegation.claim", "task", task_id, effect="write", risk="medium"
        ):
            return refusal
        try:
            return json.dumps(delegation.claim_task(task_id, actor=ACTOR))
        except ValueError as exc:
            return json.dumps({"error": str(exc)})


@mcp.tool()
def report_progress(task_id: int, note: str) -> str:
    """Append a worklog entry to your delegated task — the sponsor reads
    this before their acceptance verdict. Report as you go."""
    record_use(ACTOR, "mcp")
    with db.transaction():
        if refusal := _policy_refusal(
            "skein.mcp.delegation.progress", "task", task_id, effect="write", risk="medium"
        ):
            return refusal
        try:
            return json.dumps(delegation.report_progress(task_id, note, actor=ACTOR))
        except ValueError as exc:
            return json.dumps({"error": str(exc)})


@mcp.tool()
def read_worklog(task_id: int, limit: int = 20) -> str:
    """Read the progress notes already on a delegated task. Read this before
    continuing work you started on an earlier day — it is where you recorded
    what you found, what you decided, and what you were waiting on."""
    record_use(ACTOR, "mcp")
    if refusal := _policy_refusal("skein.mcp.worklog.read", "task", task_id):
        return refusal
    try:
        # actor=ACTOR is the door, and the limit is clamped in the service —
        # this twin passed the model's number straight into LIMIT, where a
        # negative value means NO limit in SQLite
        return json.dumps(
            {
                "task_id": task_id,
                "worklog": delegation.list_worklog(task_id, limit, actor=ACTOR),
            }
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def submit_for_acceptance(task_id: int, summary: str) -> str:
    """Submit your delegated task for the sponsor's acceptance. ALWAYS a
    proposal — never claim the task is done after calling this; say it
    awaits the sponsor's verdict."""
    record_use(ACTOR, "mcp")
    with db.transaction():
        if refusal := _policy_refusal(
            "skein.mcp.delegation.submit", "task", task_id, effect="write", risk="high"
        ):
            return refusal
        try:
            return json.dumps(delegation.submit_completion(task_id, summary, actor=ACTOR))
        except ValueError as exc:
            return json.dumps({"error": str(exc)})


@mcp.tool()
def list_tasks(status: str = "", assignee: str = "") -> str:
    """List team tasks, optionally filtered by status (todo|in_progress|blocked|done)
    and/or assignee."""
    record_use(ACTOR, "mcp")
    if refusal := _policy_refusal("skein.mcp.tasks.read", "task"):
        return refusal
    with db.read_transaction():
        rows = work.list_tasks(status=status, assignee=assignee)
        contexts = work.task_collection_policy_contexts(rows, scope.NOBODY)
        return json.dumps(
            [
                row
                for row in rows
                if _policy_permits(
                    "skein.mcp.tasks.read",
                    "task",
                    int(row["id"]),
                    contexts[int(row["id"])],
                )
            ]
        )


@mcp.tool()
def log_decision(title: str, decision: str, context: str = "") -> str:
    """Record a team decision with rationale in the decision log."""
    record_use(ACTOR, "mcp")
    payload: dict[str, Any] = {
        "title": title,
        "decision": decision,
        "context": context,
        "decided_by": ACTOR,
    }
    return gated_write(
        "decision",
        "create",
        payload,
        lambda: collab.record_decision(**payload, actor=ACTOR, origin="agent"),
        actor=ACTOR,
    )


@mcp.tool()
def add_blocker(title: str, detail: str = "", impact: str = "medium") -> str:
    """File a blocker (impact: low|medium|high|critical drives escalation speed)."""
    record_use(ACTOR, "mcp")
    payload: dict[str, Any] = {"title": title, "detail": detail, "owner": ACTOR, "impact": impact}
    return gated_write(
        "blocker",
        "create",
        payload,
        lambda: blockers_svc.raise_blocker(**payload, actor=ACTOR, origin="agent"),
        actor=ACTOR,
    )


@mcp.tool()
def search_workspace(query: str) -> str:
    """Full-text search everything the team has recorded: tasks, decisions,
    notes, blockers, questions, lessons, engagements. Use before re-deciding
    or re-researching anything."""
    record_use(ACTOR, "mcp")
    if refusal := _policy_refusal("skein.mcp.search.read", "search"):
        return refusal
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.mcp.search.read",
        "mcp",
        scope.NOBODY,
        agent=ACTOR,
        tool="skein.mcp.search.read",
    )
    with db.read_transaction():
        return json.dumps(search.search(query, row_filter=policy.filter_resources))


@mcp.tool()
def save_knowledge(topic: str, content: str) -> str:
    """Save a note to the shared team knowledge base."""
    record_use(ACTOR, "mcp")
    payload: dict[str, Any] = {"topic": topic, "content": content, "author": ACTOR}
    return gated_write(
        "note",
        "create",
        payload,
        lambda: collab.save_note(**payload, actor=ACTOR, origin="agent"),
        actor=ACTOR,
    )


@mcp.tool()
def remember(content: str, topic: str = "") -> str:
    """Persist a durable cross-thread memory (preferences, standing context).
    Gated: memories steer every future conversation, so this may file a
    proposal for human review instead of writing directly."""
    record_use(ACTOR, "mcp")
    if len(content) > 2000 or len(topic) > 100:
        return json.dumps({"error": "keep memories under 2000 characters (topic 100)"})
    return gated_write(
        "memory",
        "create",
        {"content": content, "topic": topic, "user": ACTOR},
        lambda: memory.remember(content, topic, user=ACTOR, actor=ACTOR, origin="agent"),
        summary=f"remember{f' [{topic}]' if topic else ''}: {content[:80]}",
        actor=ACTOR,
    )


@mcp.tool()
def get_context_pack(engagement_id: int = 0) -> str:
    """The team context pack (org-brain): decisions, engagement health,
    lessons, conventions. Load before working on anything team-related.
    Pass engagement_id for the scoped single-engagement pack (cheaper,
    focused — for delegated work)."""
    record_use(ACTOR, "mcp")
    if refusal := _policy_refusal("skein.mcp.context.read", "engagement", engagement_id):
        return refusal
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.mcp.context.read",
        "mcp",
        scope.NOBODY,
        agent=ACTOR,
        tool="skein.mcp.context.read",
    )
    with db.read_transaction():
        if engagement_id:
            attributes = domain_policy_context.existing_scoped(
                "engagement", engagement_id, scope.NOBODY
            )
            if not attributes or not policy.permits("engagement", engagement_id, attributes):
                return json.dumps({"error": f"no engagement #{engagement_id}"})
            return json.dumps(
                {
                    "engagement": engagement_id,
                    "content": context_pack.build_engagement_pack(engagement_id),
                }
            )
        all_projects = policy.allows_all_projects()
        return json.dumps(
            context_pack.get_pack(
                actor=ACTOR,
                resource_filter=None if all_projects else policy.permits,
            )
        )


@mcp.tool()
def my_inbox() -> str:
    """Ambient inbox for this agent identity: delegated tasks, questions,
    rejected proposals with reviewer notes, unread notifications."""
    record_use(ACTOR, "mcp")
    if refusal := _policy_refusal("skein.mcp.inbox.read", "inbox"):
        return refusal
    from .services.users import ensure_agent_identity

    ensure_agent_identity(ACTOR, owner="mcp")
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.mcp.inbox.read",
        "mcp",
        scope.NOBODY,
        agent=ACTOR,
        tool="skein.mcp.inbox.read",
    )
    with db.read_transaction():
        return json.dumps(
            delegation.agent_inbox(
                ACTOR,
                task_filter=lambda task_id, attributes: policy.permits("task", task_id, attributes),
            )
        )


@mcp.tool()
def portfolio_health() -> str:
    """Engagement health (red/yellow/green) with receipts."""
    record_use(ACTOR, "mcp")
    if refusal := _policy_refusal("skein.mcp.portfolio.read", "portfolio"):
        return refusal
    with db.read_transaction():
        rows = portfolio.engagement_health()
        contexts = domain_policy_context.resource_contexts(
            [("engagement", int(row["id"])) for row in rows], scope.NOBODY
        )
        return json.dumps(
            [
                row
                for row in rows
                if _policy_permits(
                    "skein.mcp.portfolio.read",
                    "engagement",
                    int(row["id"]),
                    contexts.get(("engagement", int(row["id"])), {}),
                )
            ]
        )


@mcp.resource("skein://context-pack")
def context_pack_resource() -> str:
    """Versioned team context pack as markdown — mountable org-brain."""
    if refusal := _policy_refusal("skein.mcp.context.read", "context-pack"):
        return refusal
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.mcp.context.read",
        "mcp",
        scope.NOBODY,
        agent=ACTOR,
        tool="skein.mcp.context.read",
    )
    with db.read_transaction():
        all_projects = policy.allows_all_projects()
        return context_pack.get_pack(
            actor=ACTOR,
            resource_filter=None if all_projects else policy.permits,
        )["content"]


def main(modules: Sequence[SkeinModule] = ()) -> None:
    # a long-lived side process must never apply schema — that is the API
    # server's job (migrations + startup jobs belong to one owner)
    pending = db.pending_migrations()
    if pending:
        import sys

        print(
            f"skein-mcp: {len(pending)} pending migration(s): {', '.join(pending)}."
            " Start the API server (or run app.db.init_db()) first, then retry.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    registry = ExtensionRegistry.build((core_module(), *tuple(modules)))
    from .extensions.registry import validate_machine_identity_ownership

    try:
        validate_machine_identity_ownership(registry, (("MCP actor", ACTOR),))
    except RuntimeError as exc:
        import sys

        print(f"skein-mcp: {exc}.", file=sys.stderr)
        raise SystemExit(1) from exc
    # reserve THIS process's identity as kind=agent before any request — the
    # API server only reserves its own env's SKEIN_MCP_USER, and a human
    # picking this name first would permanently shadow the agent
    from .services.users import ensure_agent_identity

    try:
        ensure_agent_identity(ACTOR, owner="mcp")
    except ValueError as exc:
        import sys

        # the migrations refusal above exits the same way: a process whose
        # whole identity is unavailable fails fast, but with the reason on
        # stderr, never a traceback the operator has to decode
        print(
            f"skein-mcp: cannot reserve '{ACTOR}': {exc}."
            " Set SKEIN_MCP_USER to a free name, or rename the existing row.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    attributes = registry.identity_attributes(ACTOR, (), True)
    roles = attributes.pop("roles", ())
    capabilities = attributes.pop("capabilities", ())
    if not isinstance(roles, (list, tuple)) or not isinstance(capabilities, (list, tuple)):
        raise RuntimeError("identity roles and capabilities must be lists or tuples")
    engine_token = set_policy_engine(registry.policy_engine)
    subject_token = set_policy_subject(
        PolicySubject(
            ACTOR,
            kind="agent",
            roles=tuple(str(value) for value in roles),
            capabilities=tuple(str(value) for value in capabilities),
            attributes=attributes,
        )
    )
    try:
        # stdio: stdout carries the protocol, so diagnostics above go to stderr.
        mcp.run()
    finally:
        reset_policy_subject(subject_token)
        reset_policy_engine(engine_token)


if __name__ == "__main__":
    main()
