"""MCP server wiring for the real agent. Configure via SKEIN_MCP_SERVERS:

    SKEIN_MCP_SERVERS='[{"name": "github", "url": "https://api.githubcopilot.com/mcp/", "auth_token": "ghp_..."}]'

Unconfigured (the default) this returns [] and costs nothing. Clients are
opened once per process and kept alive so tools stay usable across requests.
"""

import asyncio
import contextlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from functools import partial
from typing import Any

from strands.types.tools import AgentTool

from .. import config
from ..extensions.policy import (
    PolicyEffect,
    PolicyInput,
    PolicyResource,
    approval_fingerprint,
    current_policy_engine,
    current_policy_subject,
)

log = logging.getLogger(__name__)
_clients: list = []
_tools: list | None = None
_lock = threading.Lock()
_loading = False
_generation = 0


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
        decision = current_policy_engine().decide(policy_input)
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
                )
            except (TypeError, ValueError):
                record("refused", self.tool_name, "review state is not serializable", actor=actor)
                _audit_mcp(actor, self.tool_name, "refused", "review_state_invalid")
                yield _refusal(tool_use, "Skein could not store this remote tool review safely.")
                return
            record("refused", self.tool_name, "review required", actor=actor)
            if self.metadata.effect == "write":
                _audit_mcp(actor, self.tool_name, "review_required", "review_required")
            yield _refusal(
                tool_use,
                f"Skein review #{proposal['id']} is required for this remote tool.",
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


def _subject_data(subject) -> dict[str, Any]:
    return {
        "name": subject.name,
        "kind": subject.kind,
        "roles": list(subject.roles),
        "groups": list(subject.groups),
        "capabilities": list(subject.capabilities),
        "attributes": dict(subject.attributes),
    }


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
    from ..extensions.policy import PolicySubject, reset_policy_engine, set_policy_engine

    saved = PolicySubject(
        name=str(subject_data.get("name") or ""),
        kind=str(subject_data.get("kind") or "human"),
        roles=tuple(str(item) for item in subject_data.get("roles") or ()),
        groups=tuple(str(item) for item in subject_data.get("groups") or ()),
        capabilities=tuple(str(item) for item in subject_data.get("capabilities") or ()),
        attributes=dict(subject_data.get("attributes") or {}),
    )
    subject = registry.refresh_subject(saved)
    policy_token = set_policy_engine(registry.policy_engine)
    try:
        events = [
            event
            async for event in governed._stream(
                _json_mapping(invocation.get("tool_use")),
                _json_mapping(invocation.get("invocation_state")),
                subject,
                str(invocation.get("agent") or ""),
                str(invocation.get("approval_fingerprint") or ""),
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
        str(value["policy_action"]),
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


def mcp_tools() -> list:
    global _loading, _tools
    # the lock guards STATE, never the connect. Held across the network I/O
    # below, it queued every concurrent agent build (threadpool workers via
    # routes/chat.py) behind one hung MCP server — up to sse_read_timeout
    # (300s) per server — and a dead integration must not take down chat.
    with _lock:
        if _tools is not None:
            return _tools
        if _loading:
            # another turn is connecting: this one goes without MCP tools
            # rather than parking a worker on someone else's network I/O
            return []
        _loading = True
        generation = _generation
    tools, clients = _connect_servers()
    with _lock:
        _loading = False
        if generation == _generation:
            _clients.extend(clients)
            _tools = tools
            return tools
    # shutdown_mcp ran mid-connect: these sessions belong to the world it
    # closed — publishing them would resurrect state shutdown just tore down
    for client in clients:
        with contextlib.suppress(Exception):
            client.__exit__(None, None, None)
    return []


def _connect_servers() -> tuple[list, list]:
    """Open every configured server, returning (tools, live clients). Never
    raises: one bad server costs its own tools and a warning, not the agent."""
    tools: list = []
    clients: list = []
    if not config.MCP_SERVERS:
        return tools, clients
    try:
        servers = json.loads(config.MCP_SERVERS)
    except ValueError:
        log.warning("SKEIN_MCP_SERVERS is not valid JSON — MCP disabled")
        return tools, clients

    if not isinstance(servers, list):
        log.warning("SKEIN_MCP_SERVERS must be a JSON list — MCP disabled")
        return tools, clients
    seen_servers: set[str] = set()
    for server in servers:
        try:
            from mcp.client.streamable_http import streamablehttp_client
            from strands.tools.mcp import MCPClient

            server_id = str(server.get("name") or "").strip()
            if not server_id or server_id in seen_servers:
                raise ValueError("each MCP server needs a unique stable name")
            seen_servers.add(server_id)
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
            client.__enter__()  # keep the session open for the process lifetime
            clients.append(client)
            found = client.list_tools_sync()
            accepted = []
            for remote_tool in found:
                metadata = _metadata(server, str(remote_tool.tool_name))
                if metadata is None:
                    log.warning(
                        "MCP tool %r from %r omitted: complete governance metadata is required",
                        remote_tool.tool_name,
                        server.get("name", url),
                    )
                    continue
                accepted.append(GovernedMCPTool(remote_tool, metadata, server_id))
            tools.extend(accepted)
            log.info(
                "MCP server '%s': %d of %d tools governed and loaded",
                server.get("name", url),
                len(accepted),
                len(found),
            )
        except Exception as exc:
            log.warning("MCP server '%s' failed to connect: %s", server.get("name", "?"), exc)
    counts: dict[str, int] = {}
    for tool in tools:
        counts[tool.tool_name] = counts.get(tool.tool_name, 0) + 1
    duplicates = {name for name, count in counts.items() if count > 1}
    if duplicates:
        log.error(
            "MCP tool names collide across servers and were omitted: %s",
            ", ".join(sorted(duplicates)),
        )
        tools = [tool for tool in tools if tool.tool_name not in duplicates]
    return tools, clients


def shutdown_mcp() -> None:
    global _tools, _generation
    with _lock:
        # the generation bump tells an in-flight load its result is stale;
        # without it the load publishes after this clear and resurrects
        # closed state. Close outside the lock — same rule as the connect.
        _generation += 1
        doomed = list(_clients)
        _clients.clear()
        _tools = None
    for client in doomed:
        with contextlib.suppress(Exception):
            client.__exit__(None, None, None)
