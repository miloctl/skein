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

import contextlib
import contextvars
import functools
import json
import logging
import os
from collections.abc import Callable, Sequence
from typing import Any

import anyio
from mcp import types as mcp_types
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import ToolAnnotations

from . import db, ratelimit
from .agents.identity import (
    requester_identity,
    reset_agent_identity,
    reset_requester_identity,
    reset_requester_viewer,
    set_agent_identity,
    set_requester_identity,
    set_requester_viewer,
)
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
    weekly,
    wording,
    work,
)
from .services import policy_context as domain_policy_context
from .services.adoption import record_use
from .tools._gate import gated_write

log = logging.getLogger(__name__)

# The standalone stdio process acts as this one identity for its whole
# life (main() sets it). The in-API endpoint sets the identity per request
# instead, so every tool reads it through _actor(), never this constant.
ACTOR = os.getenv("SKEIN_MCP_USER", "mcp-agent")
mcp = FastMCP("skein")
REMOTE_PATH = "/api/mcp-server"
# one line per tool, for the context pack's "How to plug in" section
TOOL_LINES: list[str] = []

# the same volume bound Skein applies to a remote server's results
# (agents/mcp_tools._RESULT_MAX_BYTES): a Skein that consumes this one must
# never see a reply it would refuse
RESULT_MAX_BYTES = 256 * 1024
BODY_MAX_BYTES = 1024 * 1024
BUSY = "The database is busy. Wait 5 seconds, then send the request again."
FAILED = "The tool failed. Read the server log for the cause."
TOO_LARGE = "The result is larger than a client accepts. Narrow the request."
ARGUMENTS_REFUSED = "The arguments were refused. Check the tool's input schema."

READ = ToolAnnotations(readOnlyHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
IDEMPOTENT_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)


# The MCP actor has its own variable, distinct from the chat agent identity
# (agents/identity.py): a body called directly while a chat identity is set
# (a test, a service) must still act as the process identity.
_current_actor: contextvars.ContextVar[str] = contextvars.ContextVar("mcp_actor", default="")


def _actor() -> str:
    """The acting identity: `<name>-mcp` set per request by remote_app,
    else SKEIN_MCP_USER (the stdio process, or a direct call)."""
    return _current_actor.get() or ACTOR


def _person() -> str:
    """Whose memories and questions these are: the person behind a remote
    call, the agent itself over stdio (where nobody else is present)."""
    return requester_identity() or _actor()


def _tool(annotations: ToolAnnotations) -> Callable:
    """Register a sync tool body as an async tool.

    The SDK runs a sync body inline on the event loop; mounted in the API,
    one database-bound call would stall every other request, so the body
    runs in a worker thread (context variables travel with it). The SDK also
    forwards any exception's text to the client: a ValueError is an input
    error whose text is written for the caller (the 4xx rule), anything
    else answers a fixed sentence and logs its class. The original function
    is returned unchanged so tests and services keep calling it directly."""

    def register(fn):
        @functools.wraps(fn)
        async def run(**kwargs):
            def body():
                # a read is presence, not an action (routes/deps.py does the
                # same): a client polling list_tasks must not inflate the tally
                record_use(_person(), "mcp", counts=not annotations.readOnlyHint)
                ratelimit.check("mcp", _person())
                return fn(**kwargs)

            return await anyio.to_thread.run_sync(functools.partial(_guarded, fn.__name__, body))

        mcp.add_tool(run, name=fn.__name__, annotations=annotations)
        TOOL_LINES.append(f"{fn.__name__} — {_first_sentence(fn.__doc__)}")
        return fn

    return register


def _first_sentence(doc: str | None) -> str:
    text = " ".join((doc or "").split())
    return text.split(". ")[0].rstrip(".")


def _guarded(name: str, body: Callable[[], str]) -> str:
    try:
        result = body()
    except ValueError as exc:
        return json.dumps({"error": str(exc)})
    except db.BUSY_ERRORS:
        # retryable, and a fixed "read the log" would say the opposite
        return json.dumps({"error": BUSY})
    except Exception as exc:
        log.warning("MCP %s failed (%s)", name, type(exc).__name__)
        return json.dumps({"error": FAILED})
    if len(result) > RESULT_MAX_BYTES:
        return json.dumps({"error": TOO_LARGE})
    return result


def _is_refusal(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except ValueError:
        return False
    return isinstance(parsed, dict) and "error" in parsed


def _install_call_guard() -> None:
    """Two things the SDK's tool handler gets wrong for Skein: a refused
    argument answers pydantic's message, rejected value included (an error
    never echoes the value it refused); and a tool's own refusal, a JSON
    object with "error", travels as success content, which a governance-
    aware client (Skein's own agents/mcp_tools.py) records as a completed
    call. Wrapping the registered handler fixes both in one place."""
    server = mcp._mcp_server
    inner = server.request_handlers[mcp_types.CallToolRequest]

    async def handler(request):
        result = await inner(request)
        call = result.root
        if not isinstance(call, mcp_types.CallToolResult):
            return result
        text = "".join(
            block.text for block in call.content if isinstance(block, mcp_types.TextContent)
        )
        if call.isError and text.startswith("Error executing tool"):
            return mcp_types.ServerResult(
                mcp_types.CallToolResult(
                    content=[mcp_types.TextContent(type="text", text=ARGUMENTS_REFUSED)],
                    isError=True,
                )
            )
        if not call.isError and _is_refusal(text):
            call.isError = True
        return result

    server.request_handlers[mcp_types.CallToolRequest] = handler


_install_call_guard()


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
    # Hold the row this decision is about before reading it — see
    # services/policy_context.py::hold_resource. It runs first inside the
    # transaction on the three WRITE tools that open one, which is what keeps
    # the lock order uniform there. The read tools call this with no ambient
    # transaction, where hold_resource returns without taking anything: a read
    # has no write to protect, and a lock taken around a single statement
    # would release before it could matter.
    if resource_id:
        with contextlib.suppress(ValueError):
            domain_policy_context.hold_resource(resource_type, int(resource_id))
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
            agent=_actor(),
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
                wording.policy_review_unsupported()
                if decision.effect == PolicyEffect.REVIEW
                else wording.workplace_policy_denied()
            ),
            "policy_effect": decision.effect.value,
        }
    )


def _opaque_refusal(action: str, resource_type: str) -> str:
    """A composite that cannot drop one denied input is refused whole, the
    rule routes/api.py::_require_opaque_project_policy applies to /week."""
    if refusal := _policy_refusal(action, resource_type):
        return refusal
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(), current_policy_subject(), action, "mcp", scope.NOBODY
    )
    if not policy.allows_all_projects() or not policy.allows_unclassified():
        return json.dumps(
            {"error": wording.workplace_policy_denied(), "policy_effect": PolicyEffect.DENY.value}
        )
    return ""


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
            agent=_actor(),
            tool=action,
            tool_effect="read",
            tool_risk="low",
        )
    )
    return decision.effect == PolicyEffect.PERMIT


@_tool(READ)
# Takes no person parameter, and must not gain one: briefing.my_day answers
# for whatever name it is handed — assigned questions, owned blockers, tasks,
# and the BODIES of unread notifications. One model-controlled argument
# enumerated any teammate's inbox over a surface whose whole identity is an
# environment variable. Pinned by tests/test_privacy.py.
def get_my_day() -> str:
    """The briefing for this agent identity: what needs attention, tasks,
    blockers, today's events."""
    if refusal := _policy_refusal("skein.mcp.briefing.read", "briefing"):
        return refusal
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.mcp.briefing.read",
        "mcp",
        scope.NOBODY,
        agent=_actor(),
        tool="skein.mcp.briefing.read",
    )
    with db.read_transaction():
        return json.dumps(
            briefing_svc.my_day(
                _actor(),
                row_filter=policy.filter_rows,
                mixed_filter=policy.filter_resources,
                allow_unclassified=policy.allows_unclassified(),
                resource_filter=policy.permits,
            )
        )


@_tool(WRITE)
def capture(text: str) -> str:
    """Quick-capture freeform text; auto-routed to task / question / note /
    decision / blocker / promise (e.g. 'todo: ship the API', 'blocked on vendor')."""
    ratelimit.check("capture", _actor())
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
        return json.dumps({"error": wording.private_feedback_agent_refusal()})
    kind, entity, payload = capture_svc.plan(text, actor=_actor(), origin="agent")
    return gated_write(
        entity,
        "create",
        payload,
        lambda: capture_svc.capture(text, actor=_actor(), origin="agent"),
        summary=f"capture ({kind}): {text.strip()[:80]}",
        actor=_actor(),
    )


@_tool(WRITE)
def create_task(
    title: str, description: str = "", assignee: str = "", priority: str = "medium"
) -> str:
    """Create a task in the team tracker. priority: low|medium|high|urgent.
    With review mode on, queues for human approval unless this agent has
    autonomous authority for tasks."""
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
        lambda: work.create_task(**payload, actor=_actor(), origin="agent"),
        actor=_actor(),
    )


@_tool(IDEMPOTENT_WRITE)
def complete_task(task_id: int) -> str:
    """Mark a task done (queued for review unless this agent is autonomous).
    For a task DELEGATED to you, use submit_for_acceptance instead — the
    sponsor's verdict is the only thing that closes delegated work."""
    return gated_write(
        "task",
        "update",
        {"status": "done"},
        lambda: work.update_task(task_id, status="done", actor=_actor(), origin="agent"),
        entity_id=task_id,
        actor=_actor(),
    )


@_tool(WRITE)
def claim_delegated_task(task_id: int) -> str:
    """Claim a task delegated to you: todo -> in_progress, sponsor notified.
    Start here before working a delegated task from my_inbox."""
    with db.transaction():
        if refusal := _policy_refusal(
            "skein.mcp.delegation.claim", "task", task_id, effect="write", risk="medium"
        ):
            return refusal
        try:
            return json.dumps(delegation.claim_task(task_id, actor=_actor()))
        except ValueError as exc:
            return json.dumps({"error": str(exc)})


@_tool(WRITE)
def report_progress(task_id: int, note: str) -> str:
    """Append a worklog entry to your delegated task — the sponsor reads
    this before their acceptance verdict. Report as you go."""
    with db.transaction():
        if refusal := _policy_refusal(
            "skein.mcp.delegation.progress", "task", task_id, effect="write", risk="medium"
        ):
            return refusal
        try:
            return json.dumps(delegation.report_progress(task_id, note, actor=_actor()))
        except ValueError as exc:
            return json.dumps({"error": str(exc)})


@_tool(READ)
def read_worklog(task_id: int, limit: int = 20) -> str:
    """Read the progress notes already on a delegated task. Read this before
    continuing work you started on an earlier day — it is where you recorded
    what you found, what you decided, and what you were waiting on."""
    if refusal := _policy_refusal("skein.mcp.worklog.read", "task", task_id):
        return refusal
    try:
        # actor=_actor() is the door, and the limit is clamped in the service —
        # this twin passed the model's number straight into LIMIT, where a
        # negative value is refused outright and a huge one is a full scan
        return json.dumps(
            {
                "task_id": task_id,
                "worklog": delegation.list_worklog(task_id, limit, actor=_actor()),
            }
        )
    except ValueError as exc:
        return json.dumps({"error": str(exc)})


@_tool(WRITE)
def submit_for_acceptance(task_id: int, summary: str) -> str:
    """Submit your delegated task for the sponsor's acceptance. ALWAYS a
    proposal — never claim the task is done after calling this; say it
    awaits the sponsor's verdict."""
    with db.transaction():
        if refusal := _policy_refusal(
            "skein.mcp.delegation.submit", "task", task_id, effect="write", risk="high"
        ):
            return refusal
        try:
            return json.dumps(delegation.submit_completion(task_id, summary, actor=_actor()))
        except ValueError as exc:
            return json.dumps({"error": str(exc)})


@_tool(READ)
def list_tasks(status: str = "", assignee: str = "", limit: int = 50, offset: int = 0) -> str:
    """List team tasks, optionally filtered by status (todo|in_progress|blocked|done)
    and/or assignee. limit (1-200) and offset page through the list."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    if refusal := _policy_refusal("skein.mcp.tasks.read", "task"):
        return refusal
    with db.read_transaction():
        rows = work.list_tasks(status=status, assignee=assignee)
        contexts = work.task_collection_policy_contexts(rows, scope.NOBODY)
        permitted = [
            row
            for row in rows
            if _policy_permits(
                "skein.mcp.tasks.read",
                "task",
                int(row["id"]),
                contexts[int(row["id"])],
            )
        ]
        policy = projection_policy.ProjectionPolicy(
            current_policy_engine(),
            current_policy_subject(),
            "skein.mcp.tasks.read",
            "mcp",
            scope.NOBODY,
            agent=_actor(),
            tool="skein.mcp.tasks.read",
        )
        page = permitted[offset : offset + limit]
        return json.dumps(work.redact_task_relationships(page, scope.NOBODY, policy.permits))


@_tool(WRITE)
def log_decision(title: str, decision: str, context: str = "") -> str:
    """Record a team decision with rationale in the decision log."""
    payload: dict[str, Any] = {
        "title": title,
        "decision": decision,
        "context": context,
        "decided_by": _actor(),
    }
    return gated_write(
        "decision",
        "create",
        payload,
        lambda: collab.record_decision(**payload, actor=_actor(), origin="agent"),
        actor=_actor(),
    )


@_tool(WRITE)
def add_blocker(title: str, detail: str = "", impact: str = "medium") -> str:
    """File a blocker (impact: low|medium|high|critical drives escalation speed)."""
    payload: dict[str, Any] = {
        "title": title,
        "detail": detail,
        "owner": _actor(),
        "impact": impact,
    }
    return gated_write(
        "blocker",
        "create",
        payload,
        lambda: blockers_svc.raise_blocker(**payload, actor=_actor(), origin="agent"),
        actor=_actor(),
    )


@_tool(READ)
def search_workspace(query: str, limit: int = 20) -> str:
    """Full-text search everything the team has recorded: tasks, decisions,
    notes, blockers, questions, lessons, engagements. Use before re-deciding
    or re-researching anything. limit is 1 to 50."""
    limit = max(1, min(int(limit), 50))
    if refusal := _policy_refusal("skein.mcp.search.read", "search"):
        return refusal
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.mcp.search.read",
        "mcp",
        scope.NOBODY,
        agent=_actor(),
        tool="skein.mcp.search.read",
    )
    with db.read_transaction():
        return json.dumps(search.search(query, limit=limit, row_filter=policy.filter_resources))


@_tool(WRITE)
def save_knowledge(topic: str, content: str) -> str:
    """Save a note to the shared team knowledge base."""
    payload: dict[str, Any] = {"topic": topic, "content": content, "author": _actor()}
    return gated_write(
        "note",
        "create",
        payload,
        lambda: collab.save_note(**payload, actor=_actor(), origin="agent"),
        actor=_actor(),
    )


@_tool(WRITE)
def remember(content: str, topic: str = "") -> str:
    """Persist a durable cross-thread memory (preferences, standing context).
    Gated: memories steer every future conversation, so this may file a
    proposal for human review instead of writing directly."""
    if len(content) > 2000 or len(topic) > 100:
        return json.dumps({"error": "keep memories under 2000 characters (topic 100)"})
    return gated_write(
        "memory",
        "create",
        {"content": content, "topic": topic, "user": _person()},
        lambda: memory.remember(content, topic, user=_person(), actor=_actor(), origin="agent"),
        summary=f"remember{f' [{topic}]' if topic else ''}: {content[:80]}",
        actor=_actor(),
    )


@_tool(READ)
def get_context_pack(engagement_id: int = 0) -> str:
    """The team context pack (org-brain): decisions, engagement health,
    lessons, conventions. Load before working on anything team-related.
    Pass engagement_id for the scoped single-engagement pack (cheaper,
    focused — for delegated work)."""
    if refusal := _policy_refusal("skein.mcp.context.read", "engagement", engagement_id):
        return refusal
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.mcp.context.read",
        "mcp",
        scope.NOBODY,
        agent=_actor(),
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
                    "content": context_pack.build_engagement_pack(
                        engagement_id,
                        resource_filter=policy.permits,
                    ),
                }
            )
        return json.dumps(
            context_pack.get_pack(
                actor=_actor(),
                resource_filter=policy.permits,
            )
        )


@_tool(READ)
def my_inbox() -> str:
    """Ambient inbox for this agent identity: delegated tasks, questions,
    rejected proposals with reviewer notes, unread notifications."""
    if refusal := _policy_refusal("skein.mcp.inbox.read", "inbox"):
        return refusal
    from .services.users import ensure_agent_identity

    ensure_agent_identity(_actor(), owner="mcp")
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.mcp.inbox.read",
        "mcp",
        scope.NOBODY,
        agent=_actor(),
        tool="skein.mcp.inbox.read",
    )
    with db.read_transaction():
        return json.dumps(
            delegation.agent_inbox(
                _actor(),
                task_filter=lambda task_id, attributes: policy.permits("task", task_id, attributes),
                resource_filter=policy.permits,
                allow_unclassified=policy.allows_unclassified(),
            )
        )


@_tool(READ)
def portfolio_health() -> str:
    """Engagement health (red/yellow/green) with receipts."""
    if refusal := _policy_refusal("skein.mcp.portfolio.read", "portfolio"):
        return refusal
    policy = projection_policy.ProjectionPolicy(
        current_policy_engine(),
        current_policy_subject(),
        "skein.mcp.portfolio.read",
        "mcp",
        scope.NOBODY,
        agent=_actor(),
        tool="skein.mcp.portfolio.read",
    )
    with db.read_transaction():
        rows = portfolio.engagement_health(resource_filter=policy.permits)
        return json.dumps(policy.filter_rows("engagement", rows))


@_tool(WRITE)
def update_task(
    task_id: int,
    status: str = "",
    assignee: str = "",
    priority: str = "",
    due_date: str = "",
    description: str = "",
    waiting_on: str = "",
) -> str:
    """Update fields on an existing task; pass only the fields to change.
    status: todo|in_progress|blocked|done. priority: low|medium|high|urgent.
    waiting_on: 'task:12', 'blocker:3', 'promise:7' or 'question:5'; '-' clears it.
    For a task delegated to you, use submit_for_acceptance to finish it."""
    payload: dict[str, Any] = {
        "status": status,
        "assignee": assignee,
        "priority": priority,
        "due_date": due_date,
        "description": description,
        "waiting_on": waiting_on,
    }
    payload = {key: value for key, value in payload.items() if value}
    if not payload:
        return json.dumps({"error": "Pass at least one field to change."})
    return gated_write(
        "task",
        "update",
        payload,
        lambda: work.update_task(task_id, **payload, actor=_actor(), origin="agent"),
        entity_id=task_id,
        actor=_actor(),
    )


@_tool(WRITE)
def ask_question(question: str, assigned_to: str = "") -> str:
    """Log a question for the team so it does not get lost. assigned_to names
    who must answer it, if known."""
    payload: dict[str, Any] = {
        "question": question,
        "asked_by": _person(),
        "assigned_to": assigned_to,
    }
    return gated_write(
        "question",
        "create",
        payload,
        lambda: collab.ask_question(**payload, actor=_actor(), origin="agent"),
        actor=_actor(),
    )


@_tool(IDEMPOTENT_WRITE)
def answer_question(question_id: int, answer: str) -> str:
    """Answer an open question and close it."""
    payload: dict[str, Any] = {"answer": answer, "answered_by": _person()}
    return gated_write(
        "question",
        "update",
        payload,
        lambda: collab.answer_question(question_id, **payload, actor=_actor(), origin="agent"),
        entity_id=question_id,
        actor=_actor(),
    )


@_tool(IDEMPOTENT_WRITE)
def resolve_blocker(blocker_id: int, resolution: str = "") -> str:
    """Mark a blocker resolved, with how it was resolved."""
    payload: dict[str, Any] = {"resolution": resolution}
    return gated_write(
        "blocker",
        "update",
        payload,
        lambda: blockers_svc.resolve_blocker(blocker_id, **payload, actor=_actor(), origin="agent"),
        entity_id=blocker_id,
        actor=_actor(),
    )


@_tool(READ)
def recall_memories(query: str = "") -> str:
    """Search the durable memories saved with remember; empty returns the
    most recent ones."""
    # the same two axes as the chat tool (tools/memory.py): the person, and
    # the workspace tier — never a model-supplied name
    with db.read_transaction():
        rows = memory.recall(query, user=_person(), engagement_id=None)
        contexts = domain_policy_context.engagement_linked_collection_contexts(
            "memory", rows, scope.NOBODY
        )
        return json.dumps(
            [
                row
                for row in rows
                if _policy_permits(
                    "skein.mcp.memories.read", "memory", int(row["id"]), contexts[int(row["id"])]
                )
            ]
        )


@_tool(READ)
def week(week: str = "") -> str:
    """The week view: commitments, what moved, and what is due. week is
    YYYY-Www, empty for the current week."""
    if refusal := _opaque_refusal("skein.mcp.week.read", "week"):
        return refusal
    with db.read_transaction():
        return json.dumps(weekly.week_view(week))


def _resource(uri: str) -> Callable:
    """The tool guard for a resource: off the event loop, no exception
    text. The original sync function is returned for direct callers."""

    def register(fn):
        @functools.wraps(fn)
        async def run() -> str:
            return await anyio.to_thread.run_sync(functools.partial(_guarded, fn.__name__, fn))

        mcp.resource(uri)(run)
        return fn

    return register


@_resource("skein://context-pack")
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
        agent=_actor(),
        tool="skein.mcp.context.read",
    )
    with db.read_transaction():
        return context_pack.get_pack(
            actor=_actor(),
            resource_filter=policy.permits,
        )["content"]


UNAVAILABLE = (
    "The MCP identity for this person is unavailable. Ask an administrator to check the roster."
)
PERSON_KEY_ONLY = "This endpoint takes a person's key. The agent identity comes from it."


def _remote_actor(user: str) -> str:
    """The agent identity a person's remote calls act through: one indexed
    read per message, reserved on first use. No per-process cache: a row an
    administrator renamed or deactivated must stop acting at once, not at
    the next restart."""
    from .services.users import MCP_SUFFIX, ensure_agent_identity

    name = f"{user}{MCP_SUFFIX}"
    row = db.query_one("SELECT kind, active FROM users WHERE name = ?", (name,))
    if row is None:
        try:
            ensure_agent_identity(name, owner="mcp")
        except ValueError as exc:
            # the reason names the row; the caller gets the fixed sentence
            log.warning("MCP identity %r unavailable: %s", name, exc)
            raise ValueError(UNAVAILABLE) from None
    elif row["kind"] != "agent" or not row["active"]:
        raise ValueError(UNAVAILABLE)
    return name


def session_manager() -> StreamableHTTPSessionManager:
    """One per lifespan entry, never per app: run() is once per instance,
    and a test client enters one app's lifespan more than once."""
    return StreamableHTTPSessionManager(app=mcp._mcp_server, json_response=True, stateless=True)


def remote_app(registry: ExtensionRegistry):
    """The in-API endpoint (REMOTE_PATH): the same tools over streamable
    HTTP, with the caller resolved per request the way a REST route does.

    Stateless and JSON-answering, so any replica handles any message and
    nothing streams. Identity goes into the context variables the chat
    turn sets (routes/chat.py): the policy subject is the PERSON, the acting
    identity is their `<name>-mcp` agent, and the requester is the person —
    so a write lands with origin agent, actor `<name>-mcp`, requested_by
    the person, and that agent earns authority on its own matrix row. The
    lifespan (main.py) creates the session manager and enters its run()
    before the first request."""
    from fastapi import HTTPException
    from starlette.concurrency import run_in_threadpool
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    from .extensions.fastapi import subject_for
    from .routes.deps import _resolve, _stash, authentication_source
    from .services import api_keys
    from .services.users import is_agent

    # the SDK logs every JSON-RPC message, arguments included, at DEBUG; a
    # root-level change must not start logging capture text and memories
    logging.getLogger("mcp").setLevel(max(logging.INFO, logging.getLogger("mcp").level))

    def identify(request: Request):
        """Every door does database work (key lookup, roster walls, crews for
        the viewer, the agent row), so this runs in the thread pool the way
        the perimeter middleware runs the same calls."""
        authorization = request.headers.get("authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        if token.startswith(api_keys.PREFIX):
            owner = api_keys.verify_key(token)
            if owner and is_agent(owner):
                raise HTTPException(status_code=403, detail=PERSON_KEY_ONLY)
        # "GET": the weak door mints a roster row for a POST it would then
        # refuse here; a read-shaped resolve names the caller without one
        user, strong, groups = _resolve(
            request.headers.get("x-user", ""), authorization, "GET", request
        )
        if not strong:
            raise HTTPException(status_code=403, detail=wording.strong_identity_required())
        actor = _remote_actor(user)
        _stash(request, user, strong, groups, authentication_source(request, authorization, strong))
        return user, actor, subject_for(request, user)

    async def endpoint(scope, receive, send) -> None:
        request = Request(scope, receive)
        manager = getattr(request.app.state, "skein_mcp_manager", None)
        if manager is None:
            await JSONResponse({"detail": "The MCP server is starting."}, status_code=503)(
                scope, receive, send
            )
            return
        # the SDK reads the whole body with no cap; nothing here needs more
        if int(request.headers.get("content-length") or 0) > BODY_MAX_BYTES:
            await JSONResponse({"detail": "The request body is too large."}, status_code=413)(
                scope, receive, send
            )
            return
        try:
            user, actor, subject = await run_in_threadpool(identify, request)
        except HTTPException as exc:
            await JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )(scope, receive, send)
            return
        except ValueError as exc:
            await JSONResponse({"detail": str(exc)}, status_code=403)(scope, receive, send)
            return
        engine_token = set_policy_engine(registry.policy_engine)
        subject_token = set_policy_subject(subject)
        actor_token = _current_actor.set(actor)
        # the chat identity too: services that attribute through
        # agents/identity.py (the gate's proposer, receipts) read that one
        agent_token = set_agent_identity(actor)
        requester_token = set_requester_identity(user)
        viewer_token = set_requester_viewer(request.state.viewer)
        try:
            await manager.handle_request(scope, receive, send)
        finally:
            reset_requester_viewer(viewer_token)
            reset_requester_identity(requester_token)
            reset_agent_identity(agent_token)
            _current_actor.reset(actor_token)
            reset_policy_subject(subject_token)
            reset_policy_engine(engine_token)

    # an object, not the function: Starlette wraps a function endpoint as a
    # GET-only request handler and answers every POST with 405
    return _RawASGI(endpoint)


class _RawASGI:
    def __init__(self, app) -> None:
        self._app = app

    async def __call__(self, scope, receive, send) -> None:
        await self._app(scope, receive, send)


def _configured_modules() -> tuple[SkeinModule, ...]:
    """Resolve the workplace composition for the standalone MCP process.

    SKEIN_MCP_MODULES names a module (dotted path) whose `modules` attribute
    is the same tuple the private ASGI composition root passes to
    create_app. Without it, the documented `python -m app.mcp_server`
    composed CORE ONLY: the API process enforced workplace policy while the
    MCP process silently ran without the workplace rules, identities, and
    tools — two policy boundaries for one deployment.
    """
    from importlib import import_module

    target = os.getenv("SKEIN_MCP_MODULES", "").strip()
    if not target:
        import sys

        # Loud, because this is the fail-open shape: a workplace deployment
        # that forgets the variable gets an MCP process without the
        # workplace policy the API process enforces, and nothing else at
        # runtime reports the split.
        print(
            "skein-mcp: SKEIN_MCP_MODULES is not set — composing core only."
            " A workplace deployment must set it to its composition module.",
            file=sys.stderr,
        )
        return ()
    try:
        composition = import_module(target)
        modules = composition.modules
    except Exception as exc:
        import sys

        print(
            f"skein-mcp: SKEIN_MCP_MODULES={target!r} does not resolve to a"
            f" module with a `modules` tuple: {exc}. Fix the value, then"
            " start the server again.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    return tuple(modules)


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
    registry = ExtensionRegistry.build((core_module(), *(tuple(modules) or _configured_modules())))
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
    agent_token = set_agent_identity(ACTOR)
    try:
        # stdio: stdout carries the protocol, so diagnostics above go to stderr.
        mcp.run()
    finally:
        reset_agent_identity(agent_token)
        reset_policy_subject(subject_token)
        reset_policy_engine(engine_token)


if __name__ == "__main__":
    main()
