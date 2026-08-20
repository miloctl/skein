"""MCP server wiring for the real agent. Configure via SKEIN_MCP_SERVERS:

    SKEIN_MCP_SERVERS='[{"name": "github", "url": "https://api.githubcopilot.com/mcp/", "auth_token": "ghp_..."}]'

SKEIN_MCP_SERVERS_FILE reads the same list from a mounted YAML file instead.

Unconfigured (the default) this returns [] and costs nothing. Clients are
opened once per process and kept alive so tools stay usable across requests.
"""

import asyncio
import contextlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from functools import partial
from typing import Any

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

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _MCPConnection:
    server_id: str
    client: Any
    tools: tuple[Any, ...]


_connections: dict[str, _MCPConnection] = {}
_tools: list | None = None
_lock = threading.Lock()
_loading = False
_generation = 0
_RETRY_BASE_SECONDS = 30.0
_RETRY_MAX_SECONDS = 300.0
_retry_state: dict[str, tuple[int, float]] = {}


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

    def __init__(self, delegate, metadata: MCPToolMetadata, server_id: str) -> None:
        super().__init__()
        self._delegate = delegate
        self.metadata = metadata
        self.server_id = server_id

    @property
    def tool_name(self) -> str:
        return str(self._delegate.tool_name)

    @property
    def tool_spec(self):
        return self._delegate.tool_spec

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
        if decision.effect == PolicyEffect.REVIEW and approved_fingerprint != fingerprint:
            from ..services import review

            try:
                invocation = {
                    "tool": self.tool_name,
                    "server": self.server_id,
                    "version": self.metadata.version,
                    "tool_use": _json_mapping(tool_use),
                    "invocation_state": _json_mapping(invocation_state),
                    "subject": _subject_data(subject),
                    "agent": actor,
                    "approval_fingerprint": fingerprint,
                }
                proposal = review.propose_extension_invocation(
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
        try:
            async with asyncio.timeout(self.metadata.timeout_seconds):
                async for event in self._delegate.stream(tool_use, invocation_state, **kwargs):
                    events.append(event)
        except TimeoutError:
            status = "completion unknown" if self.metadata.effect == "write" else "timed out"
            record("failed", self.tool_name, status, actor=actor)
            _audit_mcp(actor, self.tool_name, "completion_unknown", "deadline_exceeded")
            yield _refusal(
                tool_use, f"The remote tool {status}.", completion_status=status.replace(" ", "_")
            )
            return
        except Exception as exc:
            declared = str(getattr(exc, "code", ""))
            code = declared if declared in self.metadata.error_codes else "remote_error"
            completion_status = (
                "completion_unknown" if self.metadata.effect == "write" else "failed"
            )
            record("failed", self.tool_name, completion_status, actor=actor)
            _audit_mcp(actor, self.tool_name, completion_status, code)
            log.exception("governed MCP tool failed", extra={"tool": self.tool_name})
            yield _refusal(
                tool_use,
                "The remote tool failed. Read the server log for the cause.",
                completion_status=completion_status,
            )
            return
        if not events or not _schema_matches(events[-1], self.metadata.output_schema):
            record("failed", self.tool_name, "invalid output", actor=actor)
            completion_status = (
                "completion_unknown" if self.metadata.effect == "write" else "failed"
            )
            _audit_mcp(actor, self.tool_name, completion_status, "invalid_output")
            yield _refusal(
                tool_use,
                "The remote tool returned data outside its declared schema.",
                completion_status=completion_status,
            )
            return
        if self.metadata.effect == "write":
            record("wrote", self.tool_name, "remote write completed", actor=actor)
            _audit_mcp(actor, self.tool_name, "completed")
        for event in events:
            yield event


def _agent_name() -> str:
    from .identity import agent_identity

    return agent_identity()


def reviewed_policy_contract(
    invocation: dict[str, Any], subject
) -> tuple[PolicyInput, dict[str, Any], bool]:
    """Resolve the current governed contract for one pending MCP verdict."""
    name = str(invocation.get("tool") or "")
    server = str(invocation.get("server") or "")
    try:
        governed = next(
            item
            for item in mcp_tools()
            if isinstance(item, GovernedMCPTool)
            and item.tool_name == name
            and item.server_id == server
        )
    except StopIteration as exc:
        raise ValueError("the reviewed remote tool is not currently composed") from exc
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
    try:
        governed = next(
            item
            for item in mcp_tools()
            if isinstance(item, GovernedMCPTool)
            and item.tool_name == name
            and item.server_id == server
        )
    except StopIteration as exc:
        raise ValueError("the reviewed remote tool is not currently composed") from exc
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


def _refusal(tool_use: dict, detail: str, *, completion_status: str = "failed") -> dict:
    return {
        "toolUseId": tool_use.get("toolUseId", "unknown"),
        "status": "error",
        "completionStatus": completion_status,
        "content": [{"text": detail}],
    }


def _metadata(server: dict, tool_name: str) -> MCPToolMetadata | None:
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
        _, loaded = _connect_servers(server_ids)
    except Exception:
        # A parser or import fault outside one server must leave the cache
        # retryable. Publishing an empty terminal cache makes recovery require
        # a process restart.
        log.exception("MCP configuration failed to load — MCP will retry")

    close_after: list[Any] = []
    with _lock:
        _loading = False
        if generation == _generation:
            loaded_ids = {connection.server_id for connection in loaded}
            for connection in loaded:
                _retry_state.pop(connection.server_id, None)
                if connection.server_id in _connections:
                    close_after.append(connection.client)
                else:
                    _connections[connection.server_id] = connection
            retry_at = time.monotonic()
            for server_id in server_ids - loaded_ids:
                failures = _retry_state.get(server_id, (0, 0.0))[0] + 1
                exponent = min(failures - 1, 4)
                delay = min(_RETRY_BASE_SECONDS * (2**exponent), _RETRY_MAX_SECONDS)
                _retry_state[server_id] = (failures, retry_at + delay)
            current = _composed_tools(_connections.values())
            if configured_ids <= set(_connections):
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
            del _retry_state[server_id]
        current = _composed_tools(_connections.values())
        if not configured_ids:
            _retry_state.clear()
            _tools = []
            return []
        if _loading:
            # A network load must not park another agent build. Already loaded
            # servers remain usable while the missing servers recover.
            return _without_reserved(current, reserved)
        missing = configured_ids - set(_connections)
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
        except RuntimeError:
            # start() fails under thread exhaustion. Only _finish_load resets
            # _loading, so leaving it set parks every retry until a process
            # restart — and the raise would reach build_agent and kill a chat
            # turn over a dead integration.
            with _lock:
                _loading = False
            log.exception("MCP retry thread failed to start — MCP will retry")
        return _without_reserved(current, reserved)
    return _without_reserved(_finish_load(ready, configured_ids, generation), reserved)


def _connect_servers(server_ids: set[str] | None = None) -> tuple[list, list[_MCPConnection]]:
    """Open selected servers. One bad server costs only its own tools."""
    connections: list[_MCPConnection] = []
    for server_id, server in _server_entries():
        if server_ids is not None and server_id not in server_ids:
            continue
        client = None
        entered = False
        try:
            from mcp.client.streamable_http import streamablehttp_client
            from strands.tools.mcp import MCPClient

            url = server["url"]
            token_env = str(server.get("auth_token_env") or "").strip()
            token = os.getenv(token_env, "") if token_env else str(server.get("auth_token") or "")
            if server.get("auth_token") and not token_env:
                log.warning(
                    "MCP server %r embeds a token in configuration; use auth_token_env",
                    server_id,
                )
            headers = {"Authorization": f"Bearer {token}"} if token else None
            client = MCPClient(partial(streamablehttp_client, url, headers=headers))
            client.__enter__()
            entered = True
            found = client.list_tools_sync()
            accepted = []
            for remote_tool in found:
                metadata = _metadata(server, str(remote_tool.tool_name))
                if metadata is None:
                    log.warning(
                        "MCP tool %r from %r omitted: complete governance metadata is required",
                        remote_tool.tool_name,
                        server_id,
                    )
                    continue
                accepted.append(GovernedMCPTool(remote_tool, metadata, server_id))
            connections.append(_MCPConnection(server_id, client, tuple(accepted)))
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
            log.warning("MCP server '%s' failed to connect: %s", server_id, exc)
    return _composed_tools(connections), connections


def shutdown_mcp() -> None:
    global _tools, _generation
    with _lock:
        # The generation bump makes an in-flight result stale. Close outside
        # the lock so a slow client shutdown cannot stop another state read.
        _generation += 1
        doomed = [connection.client for connection in _connections.values()]
        _connections.clear()
        _retry_state.clear()
        _tools = None
    for client in doomed:
        with contextlib.suppress(Exception):
            client.__exit__(None, None, None)
