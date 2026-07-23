"""MCP server wiring for the real agent. Configure via STRANDS_MCP_SERVERS:

    STRANDS_MCP_SERVERS='[{"name": "github", "url": "https://api.githubcopilot.com/mcp/", "auth_token": "ghp_..."}]'

Unconfigured (the default) this returns [] and costs nothing. Clients are
opened once per process and kept alive so tools stay usable across requests.
"""

import json
import logging
import threading

from .. import config

log = logging.getLogger(__name__)
_clients: list = []
_tools: list | None = None
_lock = threading.Lock()


def mcp_tools() -> list:
    global _tools
    with _lock:  # concurrent first agent builds must not double-connect
        if _tools is not None:
            return _tools
        return _load_tools()


def _load_tools() -> list:
    global _tools
    _tools = []
    if not config.MCP_SERVERS:
        return _tools
    try:
        servers = json.loads(config.MCP_SERVERS)
    except ValueError:
        log.warning("STRANDS_MCP_SERVERS is not valid JSON — MCP disabled")
        return _tools

    for server in servers:
        try:
            from mcp.client.streamable_http import streamablehttp_client
            from strands.tools.mcp import MCPClient

            url = server["url"]
            headers = (
                {"Authorization": f"Bearer {server['auth_token']}"}
                if server.get("auth_token") else None
            )
            client = MCPClient(
                lambda url=url, headers=headers: streamablehttp_client(url, headers=headers)
            )
            client.__enter__()  # keep the session open for the process lifetime
            _clients.append(client)
            tools = client.list_tools_sync()
            _tools.extend(tools)
            log.info("MCP server '%s': %d tools", server.get("name", url), len(tools))
        except Exception as exc:
            log.warning("MCP server '%s' failed to connect: %s",
                        server.get("name", "?"), exc)
    return _tools


def shutdown_mcp() -> None:
    global _tools
    for client in _clients:
        try:
            client.__exit__(None, None, None)
        except Exception:
            pass
    _clients.clear()
    _tools = None
