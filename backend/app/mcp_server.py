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
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import db, ratelimit
from .services import blockers as blockers_svc
from .services import briefing as briefing_svc
from .services import capture as capture_svc
from .services import collab, context_pack, delegation, memory, portfolio, search, work
from .services.adoption import record_use
from .tools._gate import gated_write

ACTOR = os.getenv("SKEIN_MCP_USER", "mcp-agent")

mcp = FastMCP("skein")


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
    return json.dumps(briefing_svc.my_day(ACTOR))


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
    try:
        return json.dumps(delegation.claim_task(task_id, actor=ACTOR))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def report_progress(task_id: int, note: str) -> str:
    """Append a worklog entry to your delegated task — the sponsor reads
    this before their acceptance verdict. Report as you go."""
    record_use(ACTOR, "mcp")
    try:
        return json.dumps(delegation.report_progress(task_id, note, actor=ACTOR))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def submit_for_acceptance(task_id: int, summary: str) -> str:
    """Submit your delegated task for the sponsor's acceptance. ALWAYS a
    proposal — never claim the task is done after calling this; say it
    awaits the sponsor's verdict."""
    record_use(ACTOR, "mcp")
    try:
        return json.dumps(delegation.submit_completion(task_id, summary, actor=ACTOR))
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@mcp.tool()
def list_tasks(status: str = "", assignee: str = "") -> str:
    """List team tasks, optionally filtered by status (todo|in_progress|blocked|done)
    and/or assignee."""
    record_use(ACTOR, "mcp")
    return json.dumps(work.list_tasks(status=status, assignee=assignee))


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
    return json.dumps(search.search(query))


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
    if engagement_id:
        return json.dumps(
            {
                "engagement": engagement_id,
                "content": context_pack.build_engagement_pack(engagement_id),
            }
        )
    return json.dumps(context_pack.get_pack(actor=ACTOR))


@mcp.tool()
def my_inbox() -> str:
    """Ambient inbox for this agent identity: delegated tasks, questions,
    rejected proposals with reviewer notes, unread notifications."""
    record_use(ACTOR, "mcp")
    from .services.users import ensure_user

    ensure_user(ACTOR, kind="agent")
    return json.dumps(delegation.agent_inbox(ACTOR))


@mcp.tool()
def portfolio_health() -> str:
    """Engagement health (red/yellow/green) with receipts."""
    record_use(ACTOR, "mcp")
    return json.dumps(portfolio.engagement_health())


@mcp.resource("skein://context-pack")
def context_pack_resource() -> str:
    """Versioned team context pack as markdown — mountable org-brain."""
    return context_pack.get_pack(actor=ACTOR)["content"]


def main() -> None:
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
    # reserve THIS process's identity as kind=agent before any request — the
    # API server only reserves its own env's SKEIN_MCP_USER, and a human
    # picking this name first would permanently shadow the agent
    from .services.users import ensure_user

    try:
        ensure_user(ACTOR, kind="agent")
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
    mcp.run()  # stdio: stdout carries the protocol, so diagnostics (the print above) go to stderr


if __name__ == "__main__":
    main()
