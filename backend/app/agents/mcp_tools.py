"""MCP server wiring for the real agent. Configure via SKEIN_MCP_SERVERS:

    SKEIN_MCP_SERVERS='[{"name": "github", "url": "https://api.githubcopilot.com/mcp/", "auth_token": "ghp_..."}]'

Unconfigured (the default) this returns [] and costs nothing. Clients are
opened once per process and kept alive so tools stay usable across requests.
"""

import asyncio
import contextlib
import json
import logging
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
    effect: str
    risk: str
    policy_action: str
    allowed_agents: tuple[str, ...]
    timeout_seconds: float
    error_codes: tuple[str, ...]
    receipt: str
    provenance: str


class GovernedMCPTool(AgentTool):
    """A remote tool that cannot execute before Skein policy decides."""

    def __init__(self, delegate, metadata: MCPToolMetadata) -> None:
        super().__init__()
        self._delegate = delegate
        self.metadata = metadata

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
        from .identity import agent_identity
        from .receipts import record

        actor = agent_identity()
        if self.metadata.allowed_agents and actor not in self.metadata.allowed_agents:
            record("refused", self.tool_name, "agent not allowed", actor=actor)
            yield _refusal(tool_use, "This agent is not allowed to use the remote tool.")
            return
        decision = current_policy_engine().decide(
            PolicyInput(
                current_policy_subject(),
                self.metadata.policy_action,
                PolicyResource("mcp-tool", self.tool_name),
                "mcp",
                agent=actor,
                tool=self.tool_name,
                tool_effect=self.metadata.effect,
                tool_risk=self.metadata.risk,
            )
        )
        if decision.effect != PolicyEffect.PERMIT:
            status = "review required" if decision.effect == PolicyEffect.REVIEW else "denied"
            record("refused", self.tool_name, status, actor=actor)
            yield _refusal(tool_use, f"Skein policy {status} for this remote tool.")
            return

        iterator = self._delegate.stream(tool_use, invocation_state, **kwargs).__aiter__()
        while True:
            try:
                event = await asyncio.wait_for(
                    anext(iterator), timeout=self.metadata.timeout_seconds
                )
            except StopAsyncIteration:
                break
            except TimeoutError:
                record("failed", self.tool_name, "remote tool timed out", actor=actor)
                yield _refusal(tool_use, "The remote tool timed out.")
                return
            yield event


def _refusal(tool_use: dict, detail: str) -> dict:
    return {
        "toolUseId": tool_use.get("toolUseId", "unknown"),
        "status": "error",
        "content": [{"text": detail}],
    }


def _metadata(server: dict, tool_name: str) -> MCPToolMetadata | None:
    value = (server.get("tools") or {}).get(tool_name)
    required = {
        "effect",
        "risk",
        "policy_action",
        "allowed_agents",
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
    try:
        timeout = float(value["timeout_seconds"])
    except (TypeError, ValueError):
        return None
    if timeout <= 0:
        return None
    return MCPToolMetadata(
        value["effect"],
        value["risk"],
        str(value["policy_action"]),
        tuple(str(item) for item in value["allowed_agents"]),
        timeout,
        tuple(str(item) for item in value["error_codes"]),
        value["receipt"],
        value["provenance"],
    )


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

    for server in servers:
        try:
            from mcp.client.streamable_http import streamablehttp_client
            from strands.tools.mcp import MCPClient

            url = server["url"]
            headers = (
                {"Authorization": f"Bearer {server['auth_token']}"}
                if server.get("auth_token")
                else None
            )
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
                accepted.append(GovernedMCPTool(remote_tool, metadata))
            tools.extend(accepted)
            log.info(
                "MCP server '%s': %d of %d tools governed and loaded",
                server.get("name", url),
                len(accepted),
                len(found),
            )
        except Exception as exc:
            log.warning("MCP server '%s' failed to connect: %s", server.get("name", "?"), exc)
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
