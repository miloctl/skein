"""MCP server wiring for the real agent. Configure via SKEIN_MCP_SERVERS:

    SKEIN_MCP_SERVERS='[{"name": "github", "url": "https://api.githubcopilot.com/mcp/", "auth_token": "ghp_..."}]'

Unconfigured (the default) this returns [] and costs nothing. Clients are
opened once per process and kept alive so tools stay usable across requests.
"""

import contextlib
import json
import logging
import threading
from functools import partial

from .. import config

log = logging.getLogger(__name__)
_clients: list = []
_tools: list | None = None
_lock = threading.Lock()
_loading = False
_generation = 0


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
            tools.extend(found)
            log.info("MCP server '%s': %d tools", server.get("name", url), len(found))
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
