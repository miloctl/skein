"""MCP server wiring for the real agent. Configure via SKEIN_MCP_SERVERS:

    SKEIN_MCP_SERVERS='[{"name": "github", "url": "https://api.githubcopilot.com/mcp/", "auth_token": "ghp_..."}]'

SKEIN_MCP_SERVERS_FILE reads the same list from a mounted YAML file instead.

Unconfigured (the default) this returns [] and costs nothing. Clients are
opened once per process and kept alive so tools stay usable across requests.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from functools import partial
from typing import Any

from strands.types.exceptions import MCPClientInitializationError
from strands.types.tools import AgentTool

from .. import config
from ..extensions.policy import (
    PolicyDecision,
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    approval_fingerprint,
    current_policy_engine,
    current_policy_subject,
)
from ..public.errors import PublicError
from ..services import scope
from ..services.mcp_servers import LIMIT as _PERSONAL_CONNECT_LIMIT
from ..services.wording import count
from .core_tools import portable_state

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _MCPConnection:
    server_id: str
    client: Any
    tools: tuple[Any, ...]
    offered: int = 0
    tier: str = "system"
    # the row's updated_at for a personal server: personal_mcp_tools reopens
    # a connection whose stamp no longer matches, which is how an edit or
    # delete in another pod reaches this one without a broadcast
    stamp: str = ""


PERSONAL = "personal"


def _is_personal(server_id: str) -> bool:
    return server_id.startswith(PERSONAL + ":")


_connections: dict[str, _MCPConnection] = {}
_tools: list | None = None
_lock = threading.Lock()
_loading = False
_generation = 0
_RETRY_BASE_SECONDS = 30.0
_RETRY_MAX_SECONDS = 300.0
_retry_state: dict[str, tuple[int, float]] = {}
# Personal servers whose last open_personal found no free connect slot.
# Read from _retry_state instead, a server that failed once before looks
# like a connect in progress, and the reviewer reads "Skein started to
# connect it" while nothing started.
_busy: set[str] = set()
# The per-call timeout bounds TIME, not volume: a server that streams inside
# its deadline can still hand the model megabytes, blowing the context or
# silently burning SKEIN_AGENT_DAILY_TOKENS on an unattended run. Measured in
# str() characters, not encoded bytes — multibyte content can carry up to 4x
# this in bytes, an accepted looseness for a volume bound.
_RESULT_MAX_BYTES = 256 * 1024
_TIMEOUT_TRIP = 2
_timeout_strikes: dict[str, int] = {}
# The entry object identifies one attempt. Deleting and re-adding the same
# server name must not let the old worker clear or publish over the new one.
_opening: dict[str, dict] = {}
# Hold a slot until the worker exits, even after forget/shutdown invalidates
# its publication. Otherwise repeated add/delete calls bypass the process cap.
_personal_slots = threading.BoundedSemaphore(_PERSONAL_CONNECT_LIMIT)
# How many of those slots one owner's connects may hold at once. An OAuth
# sign-in holds its slot while it waits up to mcp_oauth.FLOW_SECONDS for the
# person, so without this one person's abandoned sign-ins would take every
# slot and every other person's server would wait behind them.
_PER_OWNER_CONNECTS = 2
_owner_connects: dict[str, int] = {}
# The SDK's list_tools_sync waits on a future with no deadline, and a server
# that answers initialize then streams keepalives never resolves it. Every
# connect runs bounded so a hostile or broken server cannot pin the thread
# that carries a chat turn, a POST, or an approval.
_LIST_TOOLS_SECONDS = 30.0
# The SDK's own startup wait, and the extra time _enter_bounded allows past
# it. The SDK's start() times out and then joins its session thread with no
# deadline, so without this a server that never answers initialize holds
# the connect slot (and the owner's share of them) until the process
# restarts.
_STARTUP_SECONDS = 30
_ENTER_GRACE_SECONDS = 15.0
# A hostile personal server can stuff every turn's context: the tool list
# and each description are bounded, and the operator-classified env tier
# stays as declared.
_PERSONAL_TOOL_CAP = 32
_PERSONAL_DESCRIPTION_CHARS = 2000


class _ResultTooLarge(Exception):
    pass


def _deadline_strike(server_id: str) -> None:
    """A per-call timeout refuses the call but leaves a hung server composed,
    so every later call pays the full timeout again. The second consecutive
    hit drops the connection; the retry path in mcp_tools() then owns
    recovery with its existing backoff."""
    with _lock:
        strikes = _timeout_strikes.get(server_id, 0) + 1
        _timeout_strikes[server_id] = strikes
        if strikes < _TIMEOUT_TRIP:
            return
        _timeout_strikes.pop(server_id, None)
    _drop_connection(server_id, "consecutive timeouts")


def _drop_connection(server_id: str, reason: str, client: Any = None) -> None:
    global _tools
    with _lock:
        connection = _connections.get(server_id)
        # only the connection the failing tool came from: a newer one cached
        # under the same id is healthy and stays
        if connection is None or (client is not None and connection.client is not client):
            return
        del _connections[server_id]
        if connection.tier == "system":
            _tools = None
        # Seed the backoff HERE: without a retry_at in the future, the next
        # mcp_tools() call treats this server as ready and reconnects in the
        # FOREGROUND — inside an agent build, on a chat turn, against the
        # server just proven broken (the exact hold the background-retry
        # comment in mcp_tools() forbids).
        _retry_state[server_id] = (1, time.monotonic() + _RETRY_BASE_SECONDS)
    log.warning("MCP server '%s' dropped after %s", server_id, reason)
    _close_in_thread([connection.client])


def _close_quietly(client) -> None:
    with contextlib.suppress(Exception):
        client.__exit__(None, None, None)


@dataclass(frozen=True)
class MCPToolMetadata:
    version: str
    effect: str
    risk: str
    policy_action: str
    allowed_agents: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    output_schema: dict[str, Any]
    timeout_seconds: float
    error_codes: tuple[str, ...]
    receipt: str
    provenance: str


class GovernedMCPTool(AgentTool):
    """A remote tool that cannot execute before Skein policy decides."""

    def __init__(
        self, delegate, metadata: MCPToolMetadata, server_id: str, tier: str = "system"
    ) -> None:
        super().__init__()
        self._delegate = delegate
        self.metadata = metadata
        self.server_id = server_id
        self.tier = tier
        # the client this tool was listed from (_connect_servers sets it). A
        # turn can hold a tool after its connection was replaced, and that
        # tool must neither run on the replacement nor drop it.
        self.client: Any = None

    @property
    def tool_name(self) -> str:
        return str(self._delegate.tool_name)

    @property
    def tool_spec(self):
        spec = self._delegate.tool_spec
        description = spec.get("description") if isinstance(spec, dict) else None
        if self.tier != PERSONAL or not isinstance(description, str):
            return spec
        if len(description) <= _PERSONAL_DESCRIPTION_CHARS:
            return spec
        return {**spec, "description": description[:_PERSONAL_DESCRIPTION_CHARS]}

    @property
    def tool_type(self) -> str:
        return "mcp-governed"

    @property
    def supports_hot_reload(self) -> bool:
        return False

    async def stream(self, tool_use, invocation_state: dict[str, Any], **kwargs: Any):
        async for event in self._stream(
            tool_use,
            invocation_state,
            current_policy_subject(),
            _agent_name(),
            "",
            None,
            **kwargs,
        ):
            yield event

    async def _stream(
        self,
        tool_use: dict[str, Any],
        invocation_state: dict[str, Any],
        subject,
        actor: str,
        approved_fingerprint: str,
        approved_decision: PolicyDecision | None = None,
        **kwargs: Any,
    ):
        from .receipts import record

        if self.metadata.allowed_agents and actor not in self.metadata.allowed_agents:
            record("refused", self.tool_name, "agent not allowed", actor=actor)
            if self.metadata.effect == "write":
                _audit_mcp(actor, self.tool_name, "refused", "agent_not_allowed")
            yield _refusal(tool_use, "This agent is not allowed to use the remote tool.")
            return
        if self.tier == PERSONAL and not await asyncio.to_thread(
            _personal_row_is_current, self.server_id, self.client
        ):
            record("refused", self.tool_name, "server changed", actor=actor)
            yield _refusal(
                tool_use,
                "The MCP server for this tool changed or was deleted, so the call did"
                " not run. Ask again.",
                completion_status="failed",
            )
            return
        missing = set(self.metadata.required_capabilities) - set(subject.capabilities)
        if missing:
            record("refused", self.tool_name, "capability required", actor=actor)
            if self.metadata.effect == "write":
                _audit_mcp(actor, self.tool_name, "refused", "capability_required")
            yield _refusal(tool_use, "This identity cannot use the remote tool.")
            return
        policy_input = PolicyInput(
            subject,
            self.metadata.policy_action,
            PolicyResource("mcp-tool", f"{self.server_id}:{self.tool_name}"),
            "mcp",
            agent=actor,
            tool=self.tool_name,
            tool_effect=self.metadata.effect,
            tool_risk=self.metadata.risk,
        )
        decision = approved_decision or current_policy_engine().decide(policy_input)
        fingerprint = approval_fingerprint(
            policy_input,
            decision,
            {
                "tool": self.tool_name,
                "server": self.server_id,
                "version": self.metadata.version,
                "input": tool_use.get("input") or {},
            },
        )
        if approved_decision is not None and approved_fingerprint != fingerprint:
            yield _refusal(
                tool_use,
                "The reviewed remote tool approval is stale.",
                completion_status="approval_stale",
            )
            return
        # A personal server's write needs a human even under PERMIT: its
        # owner classified nothing, and the authority matrix has no mcp-tool
        # level that could relax it (delegation.set_authority refuses the
        # entity). The engine's decision object stays as decided —
        # services/review.py recomputes the approval fingerprint from it, and
        # a substituted REVIEW decision would stale every approval.
        # The database calls run on a worker thread (asyncio.to_thread copies
        # the context): on the event loop, a pool wait freezes every chat
        # stream in the process.
        needs_review = decision.effect == PolicyEffect.REVIEW or (
            self.tier == PERSONAL
            and (
                self.metadata.effect == "write"
                or not await asyncio.to_thread(
                    _first_use_approved, self.server_id, self.tool_name, self.metadata.version
                )
            )
        )
        if needs_review and approved_fingerprint != fingerprint:
            from ..services import review

            try:
                invocation = {
                    "tool": self.tool_name,
                    "server": self.server_id,
                    "version": self.metadata.version,
                    "tool_use": _json_mapping(tool_use),
                    "invocation_state": _json_mapping(portable_state(invocation_state)),
                    "subject": _subject_data(subject),
                    "agent": actor,
                    "approval_fingerprint": fingerprint,
                }
                proposal = await asyncio.to_thread(
                    review.propose_extension_invocation,
                    "mcp_tool",
                    {
                        "tool": self.tool_name,
                        "server": self.server_id,
                        "version": self.metadata.version,
                        "agent": actor,
                    },
                    invocation,
                    summary=f"Run governed remote tool {self.tool_name}",
                    actor=actor,
                    requested_by=subject.name,
                    policy_obligations=decision.obligations,
                    approver_groups=decision.approver_groups,
                    approver_capabilities=decision.approver_capabilities,
                    # a personal server runs on its owner's credential and
                    # returns their data: reviewed at the workspace tier, any
                    # teammate could approve the call and read the result.
                    # It stays private under separated duties too
                    # (review._check_separation). Policy-named approvers keep
                    # the workspace review, or nobody qualified could read it.
                    review_visibility=(
                        scope.PRIVATE
                        if review.requester_judges(
                            subject.name if subject.kind == "human" and subject.strong else "",
                            decision.approver_groups,
                            decision.approver_capabilities,
                        )
                        or (
                            self.tier == PERSONAL
                            and not decision.approver_groups
                            and not decision.approver_capabilities
                        )
                        else scope.WORKSPACE
                    ),
                    review_owner=subject.name,
                    policy_input=policy_input,
                )
            except (TypeError, ValueError):
                record("refused", self.tool_name, "review state is not serializable", actor=actor)
                _audit_mcp(actor, self.tool_name, "refused", "review_state_invalid")
                yield _refusal(tool_use, "Skein could not store this remote tool review safely.")
                return
            record(
                "queued",
                self.tool_name,
                "review required",
                int(proposal["id"]),
                actor=actor,
            )
            if self.metadata.effect == "write":
                _audit_mcp(actor, self.tool_name, "review_required", "review_required")
            yield _refusal(
                tool_use,
                f"Skein review #{proposal['id']} is required for this remote tool.",
                completion_status="review_required",
            )
            return
        if decision.effect == PolicyEffect.DENY:
            status = "denied"
            record("refused", self.tool_name, status, actor=actor)
            if self.metadata.effect == "write":
                _audit_mcp(actor, self.tool_name, "refused", decision.effect.value)
            yield _refusal(tool_use, "Skein policy denied this remote tool.")
            return
        events = []
        seen_bytes = 0
        stream = self._delegate.stream(tool_use, invocation_state, **kwargs)
        try:
            async with asyncio.timeout(self.metadata.timeout_seconds):
                async for event in stream:
                    seen_bytes += len(str(event))
                    if seen_bytes > _RESULT_MAX_BYTES:
                        # Close the generator NOW, not at async-gen GC: raising
                        # out of the for body leaves it suspended at a yield
                        # with the remote HTTP stream pinned open. Bounded, so
                        # a hung transport cannot turn the cap into a stall.
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(stream.aclose(), timeout=5)
                        raise _ResultTooLarge
                    events.append(event)
        except TimeoutError:
            status = "completion_unknown" if self.metadata.effect == "write" else "timed_out"
            record("failed", self.tool_name, status.replace("_", " "), actor=actor)
            _audit_mcp(actor, self.tool_name, "completion_unknown", "deadline_exceeded")
            _deadline_strike(self.server_id)
            yield _refusal(
                tool_use,
                _told("The remote tool did not answer in time.", status),
                completion_status=status,
            )
            return
        except _ResultTooLarge:
            # The remote already executed and was mid-result, so a write's
            # completion is unknown, the same as a timeout after dispatch.
            completion_status = (
                "completion_unknown" if self.metadata.effect == "write" else "failed"
            )
            record("failed", self.tool_name, "output too large", actor=actor)
            _audit_mcp(actor, self.tool_name, completion_status, "output_too_large")
            yield _refusal(
                tool_use,
                _told("The remote tool returned more data than Skein accepts.", completion_status),
                completion_status=completion_status,
            )
            return
        except MCPClientInitializationError as exc:
            # MCPClient.call_tool_async checks its session before it sends,
            # so the call never ran: failed, never completion unknown. The
            # session does not come back, so the connection is dropped and
            # rebuilt with the retry backoff. Kept, every later call on this
            # process fails the same way while Settings shows "connected".
            _drop_connection(self.server_id, "a closed session", self.client)
            if approved_fingerprint:
                # a reviewed call: nothing ran, so the approval must not
                # stand. services/review.py resets the proposal to pending
                # and answers 503; recorded as approved, the review page
                # would show a call that never happened as done.
                raise MCPServerNotReady(
                    "MCP_CONNECTION_CLOSED",
                    "The connection to the MCP server for this tool closed, so the call"
                    " did not run. Skein reconnects it. Wait 30 seconds, then approve again.",
                    retry_after=int(_RETRY_BASE_SECONDS),
                ) from exc
            record("failed", self.tool_name, "failed", actor=actor)
            _audit_mcp(actor, self.tool_name, "failed", "session_closed")
            yield _refusal(
                tool_use,
                "The connection to the remote tool's server closed, so the call did not"
                " run. Skein reconnects it. Try again in 30 seconds.",
                completion_status="failed",
            )
            return
        except asyncio.CancelledError:
            # The stop button cancels the turn's task (team_agent, chat), and
            # CancelledError passes `except Exception`. The request can
            # already be out, so a write without this leaves no receipt and
            # no ledger row for a change that can have happened.
            if self.metadata.effect == "write":
                record("failed", self.tool_name, "completion unknown", actor=actor)
                _audit_mcp(actor, self.tool_name, "completion_unknown", "cancelled")
            raise
        except Exception as exc:
            declared = str(getattr(exc, "code", ""))
            code = declared if declared in self.metadata.error_codes else "remote_error"
            completion_status = (
                "completion_unknown" if self.metadata.effect == "write" else "failed"
            )
            record("failed", self.tool_name, completion_status, actor=actor)
            _audit_mcp(actor, self.tool_name, completion_status, code)
            log.warning(
                "governed MCP tool failed (tool=%s error=%s)",
                self.tool_name,
                type(exc).__name__,
            )
            yield _refusal(
                tool_use,
                _told(
                    "The remote tool failed. Read the server log for the cause.", completion_status
                ),
                completion_status=completion_status,
            )
            return
        # A completed stream proves the server answers: only CONSECUTIVE
        # timeouts may trip the connection, or one slow call per hour would
        # eventually drop a healthy server.
        with _lock:
            _timeout_strikes.pop(self.server_id, None)
        # The SDK delegate yields a ToolResultEvent ENVELOPE — a dict shaped
        # {"type": "tool_result", "tool_result": {...}} — and the declared
        # output_schema describes the RESULT inside. Validating the envelope
        # refused every valid result from a schema with required fields, and
        # passed everything for a bare {"type": "object"}.
        last = events[-1] if events else None
        result = last.get("tool_result", last) if isinstance(last, dict) else last
        if isinstance(result, dict) and result.get("status") == "error":
            # MCPClient.call_tool_async catches its own transport faults and
            # the server's isError answers and returns them as a result, so
            # the except clauses above never see them. A write can have run
            # in part before it failed: completion unknown, never "wrote".
            completion_status = (
                "completion_unknown" if self.metadata.effect == "write" else "failed"
            )
            record("failed", self.tool_name, completion_status, actor=actor)
            _audit_mcp(actor, self.tool_name, completion_status, "remote_error")
            result["completionStatus"] = completion_status
            # Every provider drops completionStatus from the request, so the
            # model reads only the SDK's "Tool execution failed: ...", which
            # invites a retry of a write that can have run.
            if completion_status == "completion_unknown":
                result["content"] = [*(result.get("content") or []), {"text": _UNKNOWN_WRITE}]
            for event in events:
                yield event
            return
        if not events or not _schema_matches(result, self.metadata.output_schema):
            record("failed", self.tool_name, "invalid output", actor=actor)
            completion_status = (
                "completion_unknown" if self.metadata.effect == "write" else "failed"
            )
            _audit_mcp(actor, self.tool_name, completion_status, "invalid_output")
            yield _refusal(
                tool_use,
                _told(
                    "The remote tool returned data outside its declared schema.", completion_status
                ),
                completion_status=completion_status,
            )
            return
        if self.metadata.effect == "write":
            record("wrote", self.tool_name, "remote write completed", actor=actor)
            _audit_mcp(actor, self.tool_name, "completed")
        for event in events:
            yield event


def _personal_row_is_current(server_id: str, client: Any = None) -> bool:
    """Whether the row this process connected with still exists unchanged.
    forget() runs only on the pod that took a delete or an edit, so a turn on
    another pod that already holds the tool would otherwise keep calling the
    old URL with the deleted credential until the turn ends."""
    from ..services.mcp_servers import entries_for

    owner = server_id[len(PERSONAL) + 1 :].rsplit(":", 1)[0]
    row = dict(entries_for(owner)).get(server_id)
    if row is None:
        forget(server_id)
        return False
    with _lock:
        connection = _connections.get(server_id)
    if connection is None or connection.stamp != row["stamp"]:
        return False
    return client is None or connection.client is client


def _first_use_approved(server: str, tool: str, version: str) -> bool:
    """A personal READ runs under policy only after one human approved it.
    Annotations are the server's own claim: a hostile server labels an
    exfiltration tool read-only, and the model can be steered to pass
    private context as its arguments. One approval per (server, tool,
    version) — a changed input contract is a new tool."""
    from .. import db

    return (
        db.query_one(
            "SELECT 1 FROM extension_review_invocations WHERE kind = 'mcp_tool'"
            " AND status = 'approved' AND invocation::jsonb ->> 'server' = ?"
            " AND invocation::jsonb ->> 'tool' = ? AND invocation::jsonb ->> 'version' = ?",
            (server, tool, version),
        )
        is not None
    )


def _agent_name() -> str:
    from .identity import agent_identity

    return agent_identity()


def reviewed_policy_contract(
    invocation: dict[str, Any], subject, *, warm: bool = True
) -> tuple[PolicyInput, dict[str, Any], bool]:
    """Resolve the current governed contract for one pending MCP verdict."""
    name = str(invocation.get("tool") or "")
    server = str(invocation.get("server") or "")
    governed = _governed(name, server, warm=warm)
    tool_use = _json_mapping(invocation.get("tool_use"))
    actor = str(invocation.get("agent") or "")
    request = PolicyInput(
        subject,
        governed.metadata.policy_action,
        PolicyResource("mcp-tool", f"{server}:{name}"),
        "mcp",
        agent=actor,
        tool=name,
        tool_effect=governed.metadata.effect,
        tool_risk=governed.metadata.risk,
    )
    contract = {
        "tool": name,
        "server": server,
        "version": governed.metadata.version,
        "input": tool_use.get("input") or {},
    }
    return request, contract, str(invocation.get("version") or "") == governed.metadata.version


class MCPServerNotReady(PublicError):
    """A personal server that this process cannot use: not connected here
    (after a restart, or on a replica that never served its owner's turn),
    backing off after a failed connect, waiting for a connect slot, signed
    out, or deleted.
    review._revalidate_policy and the apply step pass it through, so the
    reviewer reads what to do instead of "Request a new review"."""

    def __init__(self, code: str, detail: str, *, retry_after: int = 0) -> None:
        super().__init__(
            code, detail, status_code=503 if retry_after else 409, retryable=bool(retry_after)
        )
        self.retry_after = retry_after


def _not_ready(owner: str, server: str, row: dict, *, warm: bool) -> MCPServerNotReady:
    if row.get("auth") == "oauth" and (not row.get("signed_in") or row.get("signin_required")):
        # the rule GET /api/mcp/servers uses for sign_in_required
        return MCPServerNotReady(
            "MCP_SIGN_IN_REQUIRED",
            "The MCP server for this tool needs a new sign-in. Its owner must sign in"
            " again in Settings, then approve again.",
        )
    if warm:
        personal_mcp_tools(owner)
    with _lock:
        opening = server in _opening
        retry = _retry_state.get(server)
        busy = server in _busy
    if not opening and busy:
        return MCPServerNotReady(
            "MCP_CONNECT_BUSY",
            "Skein is connecting other MCP servers right now, so this one waits its"
            " turn. Wait 10 seconds, then approve again.",
            retry_after=10,
        )
    # rounded up: truncated, a backoff with 0.4 seconds left reads as 0 and
    # is reported as a connect that has started
    wait = 0 if opening or retry is None else math.ceil(retry[1] - time.monotonic())
    if wait > 0:
        return MCPServerNotReady(
            "MCP_SERVER_UNAVAILABLE",
            f"The MCP server for this tool did not answer. Skein tries it again in"
            f" {count(wait, 'second')}. Approve again after that, or reject the proposal.",
            retry_after=wait,
        )
    return MCPServerNotReady(
        "MCP_SERVER_CONNECTING",
        "The MCP server for this tool is not connected yet. Skein started to"
        " connect it. Wait 10 seconds, then approve again.",
        retry_after=10,
    )


def _system_not_ready(server: str) -> MCPServerNotReady:
    """A configured shared server this process has not connected. The load
    runs on its own thread, as the retry path in mcp_tools() does."""
    # read before the load starts: the answer describes this request, and a
    # fast load that fails first would otherwise change it mid-reply
    with _lock:
        retry = _retry_state.get(server)
    try:
        threading.Thread(target=mcp_tools, daemon=True, name="skein-mcp-retry").start()
    except RuntimeError:
        log.warning("MCP load thread failed to start — MCP will retry")
    wait = math.ceil(retry[1] - time.monotonic()) if retry else 0
    if wait > 0:
        return MCPServerNotReady(
            "MCP_SERVER_UNAVAILABLE",
            f"The MCP server for this tool did not answer. Skein tries it again in"
            f" {count(wait, 'second')}. Approve again after that, or reject the proposal.",
            retry_after=wait,
        )
    return MCPServerNotReady(
        "MCP_SERVER_CONNECTING",
        "The MCP server for this tool is not connected yet. Skein started to"
        " connect it. Wait 10 seconds, then approve again.",
        retry_after=10,
    )


def _governed(name: str, server: str, *, warm: bool = True) -> GovernedMCPTool:
    """The currently composed wrapper for one (server, tool). A personal
    server is looked up in the cache only, never opened here: this runs in
    the REVIEWER's request, inside the approval transaction with the proposal
    row held (services/review.py), and a connect there would pin that hold
    for the whole startup timeout. A server that is not ready answers
    MCPServerNotReady, and a cold one starts its owner's background
    discovery first unless the caller passes warm=False (a rejection runs
    nothing, so it never needs the connection). The connection cache is per process: without this, a
    restart or another replica refuses the approval until the owner chats
    there."""
    if _is_personal(server):
        owner = server[len(PERSONAL) + 1 :].rsplit(":", 1)[0]
        from ..services.mcp_servers import entries_for

        # Read the row on a cache hit too: forget() runs only on the pod that
        # took the delete, so another pod still holds the deleted credential
        # and would run the call against the old URL.
        row = dict(entries_for(owner)).get(server)
        if row is None:
            forget(server)
            # "Request a new review" cannot help: a new review needs the
            # server too. Rejecting is the one way this proposal leaves.
            raise MCPServerNotReady(
                "MCP_SERVER_DELETED",
                "The MCP server for this tool was deleted, so the call cannot run."
                " Reject the proposal.",
            )
        with _lock:
            connection = _connections.get(server)
        if connection is None or connection.stamp != row["stamp"]:
            raise _not_ready(owner, server, row, warm=warm)
        pool = list(connection.tools)
    else:
        # The cache only, never mcp_tools(): before the first load that
        # call connects every shared server in the foreground (up to the
        # 30-second startup wait each), holding this verdict's database
        # connection the whole time.
        with _lock:
            connected = any(c.server_id == server for c in _system_connections())
            pool = list(_tools) if _tools is not None else _composed_tools(_system_connections())
        if not connected and server in {server_id for server_id, _ in _server_entries()}:
            # configured but down: "not composed" would clear the approver
            # requirement on reject and tell the reviewer to request a new
            # review that fails the same way until the server is back
            raise _system_not_ready(server)
    for item in pool:
        if (
            isinstance(item, GovernedMCPTool)
            and item.tool_name == name
            and item.server_id == server
        ):
            return item
    raise ValueError("the reviewed remote tool is not currently composed")


def _subject_data(subject) -> dict[str, Any]:
    from ..extensions.policy import policy_subject_data

    return policy_subject_data(subject)


def _json_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError("reviewed MCP state must be a mapping")
    encoded = json.dumps(value)
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("reviewed MCP state must be a mapping")
    return decoded


async def execute_reviewed_mcp(invocation: dict[str, Any], registry) -> dict[str, Any]:
    """Resume one exact remote call through its current governed wrapper."""
    name = str(invocation.get("tool") or "")
    server = str(invocation.get("server") or "")
    governed = _governed(name, server)
    if str(invocation.get("version") or "") != governed.metadata.version:
        raise ValueError("the reviewed remote tool contract has changed")
    subject_data = invocation.get("subject")
    if not isinstance(subject_data, dict):
        raise ValueError("the reviewed remote tool identity is invalid")
    from ..extensions.policy import (
        policy_decision_from_data,
        policy_subject_from_data,
        reset_policy_engine,
        set_policy_engine,
    )

    saved = policy_subject_from_data(subject_data)
    subject = registry.refresh_subject(saved)
    decision_data = invocation.get("_approval_decision")
    approved_decision = (
        policy_decision_from_data(decision_data) if isinstance(decision_data, dict) else None
    )
    policy_token = set_policy_engine(registry.policy_engine)
    try:
        events = [
            event
            async for event in governed._stream(
                _json_mapping(invocation.get("tool_use")),
                _json_mapping(invocation.get("invocation_state")),
                subject,
                str(invocation.get("agent") or ""),
                str(
                    invocation.get("_approval_grant")
                    or invocation.get("approval_fingerprint")
                    or ""
                ),
                approved_decision,
            )
        ]
    finally:
        reset_policy_engine(policy_token)
    last = events[-1] if events else {}
    # The SDK envelope has no status. Reading it reports successful calls as failed.
    last = last.get("tool_result", last)
    return {
        "status": (
            "completed"
            if last.get("status") == "success"
            else str(last.get("completionStatus") or "failed")
        ),
        "events": events,
    }


def _schema_matches(value: Any, schema: dict[str, Any]) -> bool:
    """Validate the small JSON Schema subset allowed for MCP result events."""
    expected = schema.get("type")
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if expected in checks and not checks[expected](value):
        return False
    if expected == "object" and isinstance(value, dict):
        required = schema.get("required") or ()
        if any(name not in value for name in required):
            return False
        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            return False
        return all(
            name not in value or _schema_matches(value[name], subschema)
            for name, subschema in properties.items()
            if isinstance(subschema, dict)
        )
    if expected == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        return all(_schema_matches(item, schema["items"]) for item in value)
    return expected in checks


# The one sentence for a write that can have run. Providers drop
# completionStatus, so this text is all the model learns about it.
_UNKNOWN_WRITE = (
    "The write can have run, so its completion is unknown. Do not retry it."
    " Check the remote system first."
)


def _told(detail: str, completion_status: str) -> str:
    return f"{detail} {_UNKNOWN_WRITE}" if completion_status == "completion_unknown" else detail


def _refusal(tool_use: dict, detail: str, *, completion_status: str = "failed") -> dict:
    return {
        "toolUseId": tool_use.get("toolUseId", "unknown"),
        "status": "error",
        "completionStatus": completion_status,
        "content": [{"text": detail}],
    }


def _derived_metadata(remote_tool) -> MCPToolMetadata:
    """Classification from the server's own annotations, for a server whose
    registrant wrote no governance block. An unannotated tool is a write
    whose blast radius nobody declared, so it lands at the top of the risk
    scale. The version is a digest of the input contract: a schema change
    between proposal and approval stales the proposal instead of replaying
    the reviewed input against a different tool."""
    annotations = getattr(getattr(remote_tool, "mcp_tool", None), "annotations", None)
    read_only = bool(getattr(annotations, "read_only_hint", False))
    destructive = getattr(annotations, "destructive_hint", None)
    effect = "read" if read_only else "write"
    if read_only:
        risk = "low"
    else:
        risk = "high" if destructive is None or destructive else "medium"
    # The first-use approval is keyed on this version, so it must change with
    # anything that steers the model: the description as much as the schema,
    # and the annotations that set effect and risk. A short checksum lets a
    # server craft a changed tool that keeps the approved version.
    dump = getattr(annotations, "model_dump", None)
    contract = json.dumps(
        {"spec": remote_tool.tool_spec, "annotations": dump(mode="json") if dump else None},
        sort_keys=True,
        default=str,
    )
    return MCPToolMetadata(
        f"sha256:{hashlib.sha256(contract.encode()).hexdigest()}",
        effect,
        risk,
        f"mcp:{effect}",
        (),
        (),
        {"type": "object"},
        30.0,
        (),
        "required",
        "service",
    )


def _metadata(server: dict, remote_tool) -> MCPToolMetadata | None:
    if server.get("derive"):
        return _derived_metadata(remote_tool)
    tool_name = str(remote_tool.tool_name)
    value = (server.get("tools") or {}).get(tool_name)
    required = {
        "version",
        "effect",
        "risk",
        "policy_action",
        "allowed_agents",
        "required_capabilities",
        "output_schema",
        "timeout_seconds",
        "error_codes",
        "receipt",
        "provenance",
    }
    if not isinstance(value, dict) or required - set(value):
        return None
    if value["effect"] not in ("none", "read", "write"):
        return None
    if value["risk"] not in ("low", "medium", "high", "critical"):
        return None
    if value["receipt"] != "required" or value["provenance"] != "service":
        return None
    policy_action = str(value["policy_action"]).strip()
    if not policy_action:
        return None
    list_fields = ("allowed_agents", "required_capabilities", "error_codes")
    if any(not isinstance(value[field], (list, tuple)) for field in list_fields):
        return None
    if any(not isinstance(item, str) for field in list_fields for item in value[field]):
        return None
    if not isinstance(value["output_schema"], dict):
        return None
    version = str(value["version"])
    if len(version.split(".")) != 3 or any(not part.isdigit() for part in version.split(".")):
        return None
    try:
        timeout = float(value["timeout_seconds"])
    except (TypeError, ValueError):
        return None
    if timeout <= 0:
        return None
    return MCPToolMetadata(
        version,
        value["effect"],
        value["risk"],
        policy_action,
        tuple(str(item) for item in value["allowed_agents"]),
        tuple(str(item) for item in value["required_capabilities"]),
        dict(value["output_schema"]),
        timeout,
        tuple(str(item) for item in value["error_codes"]),
        value["receipt"],
        value["provenance"],
    )


def _audit_mcp(actor: str, tool: str, status: str, error_code: str = "") -> None:
    from ..services.tool_audit import record_tool_execution

    record_tool_execution(actor=actor, tool=tool, status=status, error_code=error_code)


def _without_reserved(tools: list, reserved_names: set[str]) -> list:
    collisions = sorted({str(getattr(tool, "tool_name", tool)) for tool in tools} & reserved_names)
    if collisions:
        log.error(
            "MCP tool names collide with local tools and were omitted: %s",
            ", ".join(collisions),
        )
    return [tool for tool in tools if str(getattr(tool, "tool_name", tool)) not in reserved_names]


def _server_entries() -> list[tuple[str, dict]]:
    if config.MCP_SERVERS_ERROR:
        log.warning("%s MCP disabled", config.MCP_SERVERS_ERROR)
        return []
    if not config.MCP_SERVERS:
        return []
    try:
        servers = json.loads(config.MCP_SERVERS)
    except ValueError:
        log.warning("SKEIN_MCP_SERVERS is not valid JSON — MCP disabled")
        return []
    if not isinstance(servers, list):
        log.warning("SKEIN_MCP_SERVERS must be a JSON list — MCP disabled")
        return []

    entries = []
    seen: set[str] = set()
    for position, server in enumerate(servers, 1):
        if not isinstance(server, dict):
            log.warning("MCP server entry %d is not a JSON object — omitted", position)
            continue
        server_id = str(server.get("name") or "").strip()
        if not server_id or server_id in seen:
            log.warning("MCP server entry %d needs a unique stable name — omitted", position)
            continue
        seen.add(server_id)
        entries.append((server_id, server))
    return entries


def _composed_tools(connections) -> list:
    tools = [tool for connection in connections for tool in connection.tools]
    counts: dict[str, int] = {}
    for tool in tools:
        counts[tool.tool_name] = counts.get(tool.tool_name, 0) + 1
    duplicates = {name for name, count in counts.items() if count > 1}
    if duplicates:
        log.error(
            "MCP tool names collide across servers and were omitted: %s",
            ", ".join(sorted(duplicates)),
        )
    return [tool for tool in tools if tool.tool_name not in duplicates]


def _finish_load(server_ids: set[str], configured_ids: set[str], generation: int) -> list:
    global _loading, _tools
    loaded: list[_MCPConnection] = []
    try:
        _, loaded = _connect_servers(
            [
                (server_id, server)
                for server_id, server in _server_entries()
                if server_id in server_ids
            ]
        )
    except Exception as exc:
        # A parser or import fault outside one server must leave the cache
        # retryable. Publishing an empty terminal cache makes recovery require
        # a process restart. The class is enough: configured URLs and remote
        # response bodies must not enter platform logs.
        log.warning("MCP configuration failed to load — MCP will retry (%s)", type(exc).__name__)

    close_after: list[Any] = []
    with _lock:
        _loading = False
        if generation == _generation:
            loaded_ids = {connection.server_id for connection in loaded}
            for connection in loaded:
                _retry_state.pop(connection.server_id, None)
                _timeout_strikes.pop(connection.server_id, None)
                if connection.server_id in _connections:
                    close_after.append(connection.client)
                else:
                    _connections[connection.server_id] = connection
            for server_id in server_ids - loaded_ids:
                _schedule_retry(server_id)
            current = _composed_tools(_system_connections())
            if configured_ids <= {c.server_id for c in _system_connections()}:
                _tools = current
            result = current
        else:
            # shutdown_mcp closed the earlier generation. Publishing these
            # sessions would resurrect state that shutdown cannot close.
            close_after.extend(connection.client for connection in loaded)
            result = []
    for client in close_after:
        with contextlib.suppress(Exception):
            client.__exit__(None, None, None)
    return result


def _schedule_retry(server_id: str) -> None:
    """Caller holds _lock."""
    failures = _retry_state.get(server_id, (0, 0.0))[0] + 1
    exponent = min(failures - 1, 4)
    delay = min(_RETRY_BASE_SECONDS * (2**exponent), _RETRY_MAX_SECONDS)
    _retry_state[server_id] = (failures, time.monotonic() + delay)


def _system_connections() -> list[_MCPConnection]:
    """Caller holds _lock. Personal connections share the cache but never
    the process-wide tool list: they belong to one person's turns."""
    return [connection for connection in _connections.values() if connection.tier == "system"]


def _close_in_thread(clients: list) -> None:
    # In a thread: closing a hung transport can block, and the caller may be
    # the event loop that carries every open SSE stream.
    for client in clients:
        with contextlib.suppress(RuntimeError):
            threading.Thread(
                target=_close_quietly, args=(client,), daemon=True, name="skein-mcp-close"
            ).start()


def forget(server_id: str) -> None:
    """Drop one cached connection (a deleted personal row)."""
    forget_owner(server_id, exact=True)


def forget_owner(person: str, *, exact: bool = False) -> None:
    """Drop every cached connection of one owner (deactivation, rename): a
    connection holds the unsealed token, and the row that authorised it is
    gone. Cross-pod, the row check in personal_mcp_tools catches up."""
    prefix = person if exact else f"{PERSONAL}:{person}:"
    with _lock:
        ids = [
            s
            for s in set(_connections) | set(_opening) | set(_retry_state)
            if (s == prefix if exact else s.startswith(prefix))
        ]
        doomed = [_connections.pop(s).client for s in ids if s in _connections]
        for s in ids:
            _opening.pop(s, None)
            _retry_state.pop(s, None)
            _timeout_strikes.pop(s, None)
            _busy.discard(s)
    _close_in_thread(doomed)


def _release_slot(owner: str) -> None:
    with _lock:
        held = _owner_connects.get(owner, 0) - 1
        if held > 0:
            _owner_connects[owner] = held
        else:
            _owner_connects.pop(owner, None)
    _personal_slots.release()


def _publish_personal(entries: list[tuple[str, dict]], generation: int, owner: str) -> None:
    from ..services.mcp_servers import entries_for

    try:
        try:
            _, loaded = _connect_servers(entries)
        except Exception as exc:
            log.warning("personal MCP servers failed to load (%s)", type(exc).__name__)
            loaded = []
        current: dict[str, dict] = {}
        try:
            # Another pod can delete or rename a row without calling forget
            # here. Recheck its id and stamp after the network wait.
            owners = {sid[len(PERSONAL) + 1 :].rsplit(":", 1)[0] for sid, _ in entries}
            for owner in owners:
                current.update(entries_for(owner))
        except Exception as exc:
            log.warning("personal MCP rows could not be checked (%s)", type(exc).__name__)
        close_after: list = []
        with _lock:
            active = set()
            for server_id, server in entries:
                if _opening.get(server_id) is not server:
                    continue
                del _opening[server_id]
                row = current.get(server_id)
                if (
                    generation == _generation
                    and row
                    and (row["id"], row["stamp"]) == (server["id"], server["stamp"])
                ):
                    active.add(server_id)
                else:
                    _retry_state.pop(server_id, None)
            loaded_ids = {connection.server_id for connection in loaded}
            for connection in loaded:
                if connection.server_id not in active or connection.server_id in _connections:
                    close_after.append(connection.client)
                    continue
                _connections[connection.server_id] = connection
                _retry_state.pop(connection.server_id, None)
            for server_id in active - loaded_ids:
                _schedule_retry(server_id)
        _close_in_thread(close_after)
    finally:
        _release_slot(owner)


def open_personal(server_id: str, server: dict, *, background: bool = False) -> bool:
    """The OAuth sign-in thread waits for its grant. Discovery never holds a
    REST worker or an agent build, including the first connection attempt.
    False when no connect slot is free: the server joins _busy, which
    _not_ready reports, and keeps a retry entry so status() lists it."""
    owner = str(server.get("owner") or "")
    with _lock:
        if server_id in _opening or server_id in _connections:
            return True
        if _owner_connects.get(owner, 0) >= _PER_OWNER_CONNECTS or not _personal_slots.acquire(
            blocking=False
        ):
            _retry_state.setdefault(server_id, (0, 0.0))
            _busy.add(server_id)
            return False
        _busy.discard(server_id)
        _owner_connects[owner] = _owner_connects.get(owner, 0) + 1
        _opening[server_id] = server
        generation = _generation
    if not background:
        _publish_personal([(server_id, server)], generation, owner)
        return True
    try:
        threading.Thread(
            target=_publish_personal,
            args=([(server_id, server)], generation, owner),
            daemon=True,
            name="skein-mcp-retry",
        ).start()
    except RuntimeError as exc:
        with _lock:
            if _opening.get(server_id) is server:
                del _opening[server_id]
                _schedule_retry(server_id)
        _release_slot(owner)
        log.warning("MCP retry thread failed to start — MCP will retry (%s)", type(exc).__name__)
    return True


# How often _sweep_personal rechecks every cached personal connection.
_SWEEP_SECONDS = 60.0
_last_sweep = 0.0


def _sweep_personal() -> None:
    """Close cached personal connections whose row is gone or changed, for
    every owner, at most once per _SWEEP_SECONDS. personal_mcp_tools
    rechecks only the owner whose turn runs, and forget_owner runs only on
    the pod that took a rename or deactivation (which delete or move the
    rows), so without this another pod keeps that owner's authenticated
    session open until it restarts."""
    global _last_sweep
    from ..services.mcp_servers import entries_for

    now = time.monotonic()
    with _lock:
        if now - _last_sweep < _SWEEP_SECONDS:
            return
        _last_sweep = now
        owners = {
            server_id[len(PERSONAL) + 1 :].rsplit(":", 1)[0]
            for server_id in _connections
            if _is_personal(server_id)
        }
    current: dict[str, dict] = {}
    for owner in owners:
        current.update(entries_for(owner))
    close_after = []
    with _lock:
        for server_id in [s for s in _connections if _is_personal(s)]:
            row = current.get(server_id)
            if row is None or row["stamp"] != _connections[server_id].stamp:
                close_after.append(_connections.pop(server_id).client)
                _retry_state.pop(server_id, None)
                _timeout_strikes.pop(server_id, None)
    _close_in_thread(close_after)


def personal_mcp_tools(person: str, reserved_names: set[str] | None = None) -> list:
    """Tools from the servers `person` registered (services/mcp_servers.py).
    Only the turn that person drives receives them: build_agent attaches
    them on its personal_tools_for argument and nothing else does. Discovery
    runs in a bounded background worker, so a dead server cannot occupy a
    REST worker or delay the owner's next turn."""
    if not person:
        return []
    from ..services.mcp_servers import entries_for

    _sweep_personal()
    entries = entries_for(person)
    wanted = dict(entries)
    mine = PERSONAL + ":" + person + ":"
    close_after: list = []
    ready: list[tuple[str, dict]] = []
    now = time.monotonic()
    with _lock:
        for server_id in [s for s in _connections if s.startswith(mine)]:
            row = wanted.get(server_id)
            if row is None or row["stamp"] != _connections[server_id].stamp:
                close_after.append(_connections.pop(server_id).client)
                _retry_state.pop(server_id, None)
                _timeout_strikes.pop(server_id, None)
        for server_id in [s for s in _opening if s.startswith(mine)]:
            row = wanted.get(server_id)
            if row is None or row["stamp"] != _opening[server_id]["stamp"]:
                del _opening[server_id]
        for server_id, server in entries:
            if server_id in _connections or server_id in _opening:
                continue
            if server.get("auth") == "oauth" and (
                not server.get("signed_in") or server.get("signin_required")
            ):
                # nothing to connect with until the person signs in
                # (agents/mcp_oauth.py start); a connect here would only
                # meet the authorization demand and refuse it
                continue
            state = _retry_state.get(server_id)
            if state is None or state[1] <= now:
                ready.append((server_id, server))
    _close_in_thread(close_after)
    for server_id, server in ready:
        open_personal(server_id, server, background=True)
    with _lock:
        tools = [
            tool
            for server_id, connection in _connections.items()
            if server_id in wanted
            for tool in connection.tools
        ]
    return _without_reserved(tools, reserved_names or set())


def status() -> list[dict]:
    """Every configured env server and every cached connection, for the
    settings surface. Never a URL or a token: the env list is the
    deployment shape /api/health withholds, and the route filters personal
    rows to their owner."""
    now = time.monotonic()
    with _lock:
        ids = (
            {server_id for server_id, _ in _server_entries()}
            | set(_connections)
            | set(_retry_state)
            | set(_opening)
        )
        rows = []
        for server_id in sorted(ids):
            connection = _connections.get(server_id)
            retry = _retry_state.get(server_id)
            rows.append(
                {
                    "server_id": server_id,
                    "tier": PERSONAL if _is_personal(server_id) else "system",
                    "connected": connection is not None,
                    "connecting": server_id in _opening,
                    "offered": connection.offered if connection else 0,
                    "retry_in_seconds": max(0, int(retry[1] - now)) if retry else None,
                    "tools": [
                        {
                            "name": tool.tool_name,
                            "effect": tool.metadata.effect,
                            "risk": tool.metadata.risk,
                        }
                        for tool in (connection.tools if connection else ())
                    ],
                }
            )
    return rows


def mcp_tools(reserved_names: set[str] | None = None) -> list:
    reserved = reserved_names or set()
    global _loading, _tools
    with _lock:
        if _tools is not None:
            return _without_reserved(_tools, reserved)

    entries = _server_entries()
    configured_ids = {server_id for server_id, _ in entries}
    with _lock:
        if _tools is not None:
            return _without_reserved(_tools, reserved)
        for server_id in set(_retry_state) - configured_ids:
            if _is_personal(server_id):
                continue
            del _retry_state[server_id]
            _timeout_strikes.pop(server_id, None)
        current = _composed_tools(_system_connections())
        if not configured_ids:
            for server_id in [s for s in _retry_state if not _is_personal(s)]:
                del _retry_state[server_id]
            _tools = []
            return []
        if _loading:
            # A network load must not park another agent build. Already loaded
            # servers remain usable while the missing servers recover.
            return _without_reserved(current, reserved)
        missing = configured_ids - {c.server_id for c in _system_connections()}
        if not missing:
            _tools = current
            return _without_reserved(current, reserved)
        now = time.monotonic()
        ready = {
            server_id for server_id in missing if _retry_state.get(server_id, (0, 0.0))[1] <= now
        }
        if not ready:
            return _without_reserved(current, reserved)
        background = any(server_id in _retry_state for server_id in ready)
        _loading = True
        generation = _generation

    if background:
        # A failed endpoint can hold the SDK transport read for 300 seconds.
        # Recovery runs outside agent construction so no later chat owns it.
        try:
            threading.Thread(
                target=_finish_load,
                args=(ready, configured_ids, generation),
                daemon=True,
                name="skein-mcp-retry",
            ).start()
        except RuntimeError as exc:
            # start() fails under thread exhaustion. Only _finish_load resets
            # _loading, so leaving it set parks every retry until a process
            # restart — and the raise would reach build_agent and kill a chat
            # turn over a dead integration.
            with _lock:
                _loading = False
            log.warning(
                "MCP retry thread failed to start — MCP will retry (%s)",
                type(exc).__name__,
            )
        return _without_reserved(current, reserved)
    return _without_reserved(_finish_load(ready, configured_ids, generation), reserved)


def _connect_servers(
    entries: list[tuple[str, dict]] | None = None,
) -> tuple[list, list[_MCPConnection]]:
    """Open the given servers (the env list by default). One bad server
    costs only its own tools."""
    connections: list[_MCPConnection] = []
    for server_id, server in _server_entries() if entries is None else entries:
        client = None
        entered = False
        try:
            from strands.tools.mcp import MCPClient

            url = server["url"]
            tier = str(server.get("tier") or "system")
            token_env = str(server.get("auth_token_env") or "").strip()
            token = os.getenv(token_env, "") if token_env else str(server.get("auth_token") or "")
            if server.get("auth_token") and not token_env and tier != PERSONAL:
                log.warning(
                    "MCP server %r embeds a token in configuration; use auth_token_env",
                    server_id,
                )
            headers = {"Authorization": f"Bearer {token}"} if token else None
            # A personal server's tools carry its name as a prefix, so two of
            # one person's servers, or a personal and an env server, cannot
            # collide. Env servers stay unprefixed: renaming their tools
            # would stale every pending proposal keyed on the old name.
            prefix = str(server.get("name") or "") if tier == PERSONAL else ""
            startup = _STARTUP_SECONDS
            if tier == PERSONAL:
                # re-checked at every connect, not only at add time: the
                # host can resolve somewhere else once the row exists.
                # Redirects are refused for the same reason — the checked
                # host could 307 the JSON-RPC POST to loopback or metadata.
                from ..services.mcp_servers import check_url

                check_url(url)
                auth = None
                if server.get("auth") == "oauth":
                    from . import mcp_oauth

                    auth = mcp_oauth.provider(server)
                    if server.get("flow") is not None:
                        # the grant runs inside this connect's first request
                        # and waits for a person; the desktop default of 30
                        # seconds would cancel it mid-sign-in
                        startup = int(mcp_oauth.FLOW_SECONDS)
                transport = partial(
                    _http_transport,
                    url,
                    headers=headers,
                    personal=True,
                    auth=auth,
                )
            else:
                transport = partial(_http_transport, url, headers=headers)
            client = (
                MCPClient(transport, prefix=prefix, startup_timeout=startup)
                if prefix
                else MCPClient(transport)
            )
            _enter_bounded(client, startup)
            entered = True
            found = _list_tools(client)
            if tier == PERSONAL and len(found) > _PERSONAL_TOOL_CAP:
                log.warning(
                    "MCP server '%s' offers %d tools; the first %d are kept",
                    server_id,
                    len(found),
                    _PERSONAL_TOOL_CAP,
                )
                found = found[:_PERSONAL_TOOL_CAP]
            accepted = []
            for remote_tool in found:
                metadata = _metadata(server, remote_tool)
                if metadata is None:
                    log.warning(
                        "MCP tool %r from %r omitted: complete governance metadata is required",
                        remote_tool.tool_name,
                        server_id,
                    )
                    continue
                governed = GovernedMCPTool(remote_tool, metadata, server_id, tier)
                governed.client = client
                accepted.append(governed)
            connections.append(
                _MCPConnection(
                    server_id,
                    client,
                    tuple(accepted),
                    len(found),
                    tier,
                    str(server.get("stamp") or ""),
                )
            )
            log.info(
                "MCP server '%s': %d of %d tools governed and loaded",
                server_id,
                len(accepted),
                len(found),
            )
        except Exception as exc:
            if entered and client is not None:
                with contextlib.suppress(Exception):
                    client.__exit__(None, None, None)
            log.warning("MCP server '%s' failed to connect (%s)", server_id, type(exc).__name__)
            flow = server.get("flow")
            if flow is not None:
                flow.unreachable = _unreachable(exc)
    return _composed_tools(connections), connections


def _unreachable(exc: BaseException) -> bool:
    """Whether a failed connect never got an answer: refused, timed out, or
    a name that did not resolve. The SDK wraps a server's answer (a 404, a
    page that is not MCP) in MCPError inside an ExceptionGroup, and a
    connect that gets nothing ends as OSError or a transport error."""
    import httpx
    import httpx2
    from mcp.shared.exceptions import MCPError

    stack: list[BaseException | None] = [exc]
    seen: set[int] = set()
    transport = False
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, MCPError):
            return False
        transport = transport or isinstance(
            current, (OSError, httpx.TransportError, httpx2.TransportError)
        )
        stack += [current.__cause__, current.__context__]
        stack += list(getattr(current, "exceptions", ()))
    return transport


def _enter_bounded(client, startup: float) -> None:
    """Open one client, giving up after its startup wait plus a grace. A
    connect that finishes after the caller gave up is closed on its own
    thread, so the session it opened does not outlive the attempt."""
    gave_up = threading.Event()

    def enter() -> None:
        client.__enter__()
        if gave_up.is_set():
            with contextlib.suppress(Exception):
                client.__exit__(None, None, None)

    pool = ThreadPoolExecutor(1, thread_name_prefix="skein-mcp-enter")
    future = pool.submit(enter)
    try:
        future.result(timeout=startup + _ENTER_GRACE_SECONDS)
    except FutureTimeout as exc:
        gave_up.set()
        # it can finish between the timeout and the flag: close it here then
        if future.done() and future.exception() is None:
            with contextlib.suppress(Exception):
                client.__exit__(None, None, None)
        raise TimeoutError("the MCP server did not finish connecting") from exc
    finally:
        # wait=False: a hung connect keeps its worker, the caller does not
        pool.shutdown(wait=False)


def _list_tools(client) -> list:
    pool = ThreadPoolExecutor(1, thread_name_prefix="skein-mcp-list")
    try:
        return list(pool.submit(client.list_tools_sync).result(timeout=_LIST_TOOLS_SECONDS))
    finally:
        # wait=False: a hung listing keeps its worker, the caller does not
        pool.shutdown(wait=False)


@contextlib.asynccontextmanager
async def _http_transport(url, *, headers=None, auth=None, personal=False):
    import httpx
    import httpx2
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

    # HTTPX2 defaults to OS trust. Keep the existing certifi / SSL_CERT_FILE /
    # SSL_CERT_DIR trust contract for configured and personal credential destinations.
    factory = (
        _no_redirect_client
        if personal
        else partial(
            httpx2.AsyncClient,
            follow_redirects=True,
            timeout=httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
            verify=httpx.create_ssl_context(),
        )
    )
    # Strands enters this on its own event-loop thread. An HTTP client made
    # outside this context can retain sockets bound to the caller's loop.
    async with (
        factory(headers=headers, auth=auth) as client,
        streamable_http_client(url, http_client=client) as streams,
    ):
        yield streams


def _no_redirect_client(headers=None, timeout=None, auth=None):
    """Personal requests validate each destination and refuse every redirect."""
    import httpx
    import httpx2
    from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

    from ..services.mcp_servers import check_url

    async def validate_destination(request: httpx2.Request) -> None:
        # OAuth emits discovery and token requests to server-supplied URLs.
        # Disabling redirects alone does not validate those destinations.
        await asyncio.to_thread(check_url, str(request.url))

    async def refuse_redirect(response: httpx2.Response) -> None:
        if 300 <= response.status_code < 400:
            raise ValueError("The MCP server returned a redirect. Use its final URL.")

    return httpx2.AsyncClient(
        follow_redirects=False,
        verify=httpx.create_ssl_context(),
        timeout=timeout or httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=MCP_DEFAULT_SSE_READ_TIMEOUT),
        headers=headers,
        auth=auth,
        event_hooks={"request": [validate_destination], "response": [refuse_redirect]},
    )


def shutdown_mcp() -> None:
    global _tools, _generation
    with _lock:
        # The generation bump makes an in-flight result stale. Close outside
        # the lock so a slow client shutdown cannot stop another state read.
        _generation += 1
        doomed = [connection.client for connection in _connections.values()]
        _connections.clear()
        _retry_state.clear()
        _timeout_strikes.clear()
        _opening.clear()
        _busy.clear()
        _tools = None
    for client in doomed:
        with contextlib.suppress(Exception):
            client.__exit__(None, None, None)
