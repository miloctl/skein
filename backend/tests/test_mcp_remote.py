"""Skein's MCP server over HTTP: the caller is resolved per request, acts
as their own `<name>-mcp` agent, a weak or agent identity is refused before
the MCP layer, tool bodies run off the event loop with the request's
context, and an unexpected error never leaks its text."""

import asyncio
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = "http://testserver/api/mcp-server"


def _key(owner: str) -> dict:
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(owner, 'r')['key']}"}


def _session(headers: dict, scenario):
    """One lifespan, one client, one MCP session. ASGITransport runs no
    lifespan, and the sync TestClient fixture's app already ran its session
    manager on another loop, so this enters the lifespan itself."""
    from app.main import app

    async def run():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as http,
            streamable_http_client(URL, http_client=http) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            await session.initialize()
            return await scenario(session)

    return asyncio.run(run())


def _raw(headers: dict) -> httpx.Response:
    from app.main import app

    async def run():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as http,
        ):
            return await http.post(
                URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "t", "version": "0"},
                    },
                },
                headers={"Accept": "application/json, text/event-stream"},
            )

    return asyncio.run(run())


def _text(result) -> str:
    return "".join(getattr(item, "text", "") for item in result.content)


def test_tools_are_listed_with_annotations(fresh_db):
    async def scenario(session):
        return (await session.list_tools()).tools

    tools = _session(_key("ava"), scenario)
    by_name = {tool.name: tool for tool in tools}
    assert len(tools) == 23
    assert by_name["get_my_day"].annotations.readOnlyHint is True
    assert by_name["capture"].annotations.readOnlyHint is False
    assert by_name["capture"].annotations.destructiveHint is False
    assert by_name["complete_task"].annotations.idempotentHint is True
    assert set(by_name["list_tasks"].inputSchema["properties"]) >= {"limit", "offset"}
    assert {"update_task", "ask_question", "answer_question", "resolve_blocker", "week"} <= set(
        by_name
    )


def test_a_write_acts_as_the_persons_mcp_agent(fresh_db, monkeypatch):
    """Proves the request context reaches the tool body in its worker
    thread: the proposal names the agent as proposer and the person as
    requester, and a second person gets a second agent."""
    from app import config, mcp_server
    from app.services import users

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    # the reservation cache is per process and this database is fresh
    monkeypatch.setattr(mcp_server, "_reserved", set())
    users.ensure_user("ava")
    users.ensure_user("bo")

    async def scenario(session):
        return _text(await session.call_tool("capture", {"text": "todo: ship the remote MCP"}))

    for person in ("ava", "bo"):
        reply = json.loads(_session(_key(person), scenario))
        assert "error" not in reply, reply
    rows = fresh_db.query("SELECT proposed_by, requested_by FROM pending_changes ORDER BY id")
    assert [(r["proposed_by"], r["requested_by"]) for r in rows] == [
        ("ava-mcp", "ava"),
        ("bo-mcp", "bo"),
    ]
    agents = fresh_db.query("SELECT name, kind FROM users WHERE name LIKE '%-mcp' ORDER BY name")
    assert [(r["name"], r["kind"]) for r in agents] == [("ava-mcp", "agent"), ("bo-mcp", "agent")]


def test_a_weak_or_agent_identity_is_refused_before_the_mcp_layer(fresh_db):
    from app.services import users

    weak = _raw({"X-User": "ava"})
    assert weak.status_code == 403
    assert "strong identity" in weak.json()["detail"]
    users.ensure_agent_identity("bot-mcp", owner="mcp")
    agent = _raw(_key("bot-mcp"))
    assert agent.status_code == 403
    assert fresh_db.query_one("SELECT 1 FROM users WHERE name = 'ava'") is None, (
        "a refused caller minted a roster row"
    )


def test_an_unexpected_error_answers_a_fixed_sentence(fresh_db, monkeypatch):
    from app import mcp_server

    def boom(*_args, **_kwargs):
        raise RuntimeError("secret detail: /var/lib/skein")

    monkeypatch.setattr(mcp_server.briefing_svc, "my_day", boom)

    async def scenario(session):
        return _text(await session.call_tool("get_my_day", {}))

    reply = _session(_key("ava"), scenario)
    assert json.loads(reply) == {"error": "The tool failed. Read the server log for the cause."}
    assert "secret detail" not in reply


def test_a_read_and_a_bounded_list_answer_over_http(fresh_db):
    from app.services import users, work

    users.ensure_user("ava")
    for n in range(3):
        work.create_task(f"task {n}", actor="ava")

    async def scenario(session):
        page = _text(await session.call_tool("list_tasks", {"limit": 2, "offset": 1}))
        week = _text(await session.call_tool("week", {}))
        return json.loads(page), json.loads(week)

    page, week = _session(_key("ava"), scenario)
    assert len(page) == 2
    assert "error" not in week
