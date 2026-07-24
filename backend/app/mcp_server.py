"""Strands platform as an MCP server — so the team's OTHER AI agents (Claude
Code sessions, custom agents) can read/write the platform natively.

Runs in-process against the same database (no HTTP hop):

    cd backend && STRANDS_MCP_USER=mario .venv/bin/python -m app.mcp_server

Claude Code registration:

    claude mcp add strands -- env STRANDS_MCP_USER=you \
        /path/to/backend/.venv/bin/python -m app.mcp_server

Writes are attributed to STRANDS_MCP_USER (shown with origin=agent).
"""

import json
import os

from mcp.server.fastmcp import FastMCP

from . import db
from .services import blockers as blockers_svc
from .services import briefing as briefing_svc
from .services import capture as capture_svc
from .services import collab, context_pack, delegation, memory, portfolio, search, work

ACTOR = os.getenv("STRANDS_MCP_USER", "mcp-agent")

mcp = FastMCP("strands-team-platform")


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
    return json.dumps(briefing_svc.my_day(user or ACTOR))


@mcp.tool()
def capture(text: str) -> str:
    """Quick-capture freeform text; auto-routed to task / question / note /
    decision / blocker (e.g. 'todo: ship the API', 'blocked on vendor')."""
    return json.dumps(capture_svc.capture(text, actor=ACTOR, origin="agent"))


@mcp.tool()
def create_task(title: str, description: str = "", assignee: str = "",
                priority: str = "medium") -> str:
    """Create a task in the team tracker. priority: low|medium|high|urgent."""
    _check_authority("task")
    return json.dumps(work.create_task(title, description, assignee=assignee,
                                       priority=priority, actor=ACTOR, origin="agent"))


@mcp.tool()
def complete_task(task_id: int) -> str:
    """Mark a task done."""
    _check_authority("task")
    return json.dumps(work.update_task(task_id, status="done", actor=ACTOR,
                                       origin="agent"))


@mcp.tool()
def list_tasks(status: str = "", assignee: str = "") -> str:
    """List team tasks, optionally filtered by status (todo|in_progress|blocked|done)
    and/or assignee."""
    return json.dumps(work.list_tasks(status=status, assignee=assignee))


@mcp.tool()
def log_decision(title: str, decision: str, context: str = "") -> str:
    """Record a team decision with rationale in the decision log."""
    _check_authority("decision")
    return json.dumps(collab.record_decision(title, decision, context,
                                             decided_by=ACTOR, actor=ACTOR,
                                             origin="agent"))


@mcp.tool()
def add_blocker(title: str, detail: str = "", impact: str = "medium") -> str:
    """File a blocker (impact: low|medium|high|critical drives escalation speed)."""
    _check_authority("blocker")
    return json.dumps(blockers_svc.raise_blocker(title, detail, owner=ACTOR,
                                                 impact=impact, actor=ACTOR,
                                                 origin="agent"))


@mcp.tool()
def search_workspace(query: str) -> str:
    """Full-text search everything the team has recorded: tasks, decisions,
    notes, blockers, questions, lessons, engagements. Use before re-deciding
    or re-researching anything."""
    return json.dumps(search.search(query))


@mcp.tool()
def save_knowledge(topic: str, content: str) -> str:
    """Save a note to the shared team knowledge base."""
    _check_authority("note")
    return json.dumps(collab.save_note(topic, content, author=ACTOR, actor=ACTOR,
                                       origin="agent"))


@mcp.tool()
def remember(content: str, topic: str = "") -> str:
    """Persist a durable cross-thread memory (preferences, standing context)."""
    return json.dumps(memory.remember(content, topic, user=ACTOR, actor=ACTOR))


@mcp.tool()
def get_context_pack() -> str:
    """The team context pack (org-brain): decisions, engagement health,
    lessons, conventions. Load before working on anything team-related."""
    return json.dumps(context_pack.get_pack(actor=ACTOR))


@mcp.tool()
def my_inbox() -> str:
    """Ambient inbox for this agent identity: delegated tasks, questions,
    rejected proposals with reviewer notes, unread notifications."""
    from .services.users import ensure_user

    ensure_user(ACTOR, kind="agent")
    return json.dumps(delegation.agent_inbox(ACTOR))


@mcp.tool()
def portfolio_health() -> str:
    """Engagement health (red/yellow/green) with receipts."""
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
