"""Strands platform as an MCP server — so the team's OTHER AI agents (Claude
Code sessions, custom agents) can read/write the platform natively.

Runs in-process against the same database (no HTTP hop):

    cd backend && STRANDS_MCP_USER=mario .venv/bin/python -m app.mcp_server

Claude Code registration:

    claude mcp add strands -- env STRANDS_MCP_USER=you \
        /path/to/backend/.venv/bin/python -m app.mcp_server

Writes are attributed to STRANDS_MCP_USER (shown with origin=agent) and go
through the same authority gate as chat-agent tools: with review mode on they
queue in /review until this agent identity earns autonomous authority — so
MCP traffic builds trust scores like everyone else's.
"""

import json
import os

from mcp.server.fastmcp import FastMCP

from . import db
from .services.adoption import record_use
from .tools._gate import gated_write
from .services import blockers as blockers_svc
from .services import briefing as briefing_svc
from .services import capture as capture_svc
from .services import collab, context_pack, delegation, memory, portfolio, search, work

ACTOR = os.getenv("STRANDS_MCP_USER", "mcp-agent")

mcp = FastMCP("skein")


def _check_authority(entity: str) -> None:
    """The MCP path is direct-write by design (a teammate's own agent), but a
    'forbidden' authority row must hold here too — it's the kill switch."""
    from .services.delegation import authority_level

    if authority_level(ACTOR, entity) == "forbidden":
        raise ValueError(f"writes to {entity} are forbidden for agent '{ACTOR}'"
                         " by the authority matrix")


@mcp.tool()
def get_my_day(user: str = "") -> str:
    """The team briefing: what needs attention, tasks, blockers, today's events."""
    record_use(ACTOR, "mcp")
    return json.dumps(briefing_svc.my_day(user or ACTOR))


@mcp.tool()
def capture(text: str) -> str:
    """Quick-capture freeform text; auto-routed to task / question / note /
    decision / blocker / commitment (e.g. 'todo: ship the API', 'blocked on vendor')."""
    record_use(ACTOR, "mcp")
    # the kill switch must cover the routed entity, not just direct tools
    kind = capture_svc.classify(text.strip())
    _check_authority({"task": "task", "question": "question", "decision": "decision",
                      "blocker": "blocker", "commitment": "commitment",
                      "note": "note"}.get(kind, "note"))
    return json.dumps(capture_svc.capture(text, actor=ACTOR, origin="agent"))


@mcp.tool()
def create_task(title: str, description: str = "", assignee: str = "",
                priority: str = "medium") -> str:
    """Create a task in the team tracker. priority: low|medium|high|urgent.
    With review mode on, queues for human approval unless this agent has
    autonomous authority for tasks."""
    record_use(ACTOR, "mcp")
    payload = dict(title=title, description=description, assignee=assignee,
                   priority=priority)
    return gated_write("task", "create", payload,
                       lambda: work.create_task(**payload, actor=ACTOR,
                                                origin="agent"), actor=ACTOR)


@mcp.tool()
def complete_task(task_id: int) -> str:
    """Mark a task done (queued for review unless this agent is autonomous)."""
    record_use(ACTOR, "mcp")
    return gated_write("task", "update", {"status": "done"},
                       lambda: work.update_task(task_id, status="done",
                                                actor=ACTOR, origin="agent"),
                       entity_id=task_id, actor=ACTOR)


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
    payload = dict(title=title, decision=decision, context=context,
                   decided_by=ACTOR)
    return gated_write("decision", "create", payload,
                       lambda: collab.record_decision(**payload, actor=ACTOR,
                                                      origin="agent"), actor=ACTOR)


@mcp.tool()
def add_blocker(title: str, detail: str = "", impact: str = "medium") -> str:
    """File a blocker (impact: low|medium|high|critical drives escalation speed)."""
    record_use(ACTOR, "mcp")
    payload = dict(title=title, detail=detail, owner=ACTOR, impact=impact)
    return gated_write("blocker", "create", payload,
                       lambda: blockers_svc.raise_blocker(**payload, actor=ACTOR,
                                                          origin="agent"), actor=ACTOR)


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
    payload = dict(topic=topic, content=content, author=ACTOR)
    return gated_write("note", "create", payload,
                       lambda: collab.save_note(**payload, actor=ACTOR,
                                                origin="agent"), actor=ACTOR)


@mcp.tool()
def remember(content: str, topic: str = "") -> str:
    """Persist a durable cross-thread memory (preferences, standing context)."""
    record_use(ACTOR, "mcp")
    return json.dumps(memory.remember(content, topic, user=ACTOR, actor=ACTOR))


@mcp.tool()
def get_context_pack() -> str:
    """The team context pack (org-brain): decisions, engagement health,
    lessons, conventions. Load before working on anything team-related."""
    record_use(ACTOR, "mcp")
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


@mcp.resource("strands://context-pack")
def context_pack_resource() -> str:
    """Versioned team context pack as markdown — mountable org-brain."""
    return context_pack.get_pack(actor=ACTOR)["content"]


def main() -> None:
    db.init_db()
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
