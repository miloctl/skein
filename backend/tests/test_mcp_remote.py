"""Skein's MCP server over HTTP: the caller is resolved per request, acts
as their own `<name>-mcp` agent, a weak or agent identity is refused before
the MCP layer, tool bodies run off the event loop with the request's
context, and an unexpected error never leaks its text."""

import asyncio
import json

import httpx
import pytest
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
    from app import config
    from app.services import users

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
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
        every = _text(await session.call_tool("list_tasks", {}))
        page = _text(await session.call_tool("list_tasks", {"limit": 2, "offset": 1}))
        week = _text(await session.call_tool("week", {}))
        return json.loads(every), json.loads(page), json.loads(week)

    every, page, week = _session(_key("ava"), scenario)
    assert [row["id"] for row in page] == [row["id"] for row in every][1:3]
    assert "error" not in week


def test_agent_key_wording_and_the_mcp_name_space(fresh_db):
    from app import mcp_server
    from app.services import users

    users.ensure_agent_identity("bot", owner="mcp")
    reply = _raw(_key("bot"))
    assert reply.status_code == 403 and reply.json()["detail"] == mcp_server.PERSON_KEY_ONLY
    # a human cannot take a person's agent name, so nobody is locked out
    with pytest.raises(ValueError):
        users.ensure_human_identity("ava-mcp")
    # a renamed or deactivated agent row stops acting at once
    users.ensure_user("ava")
    _session(_key("ava"), lambda session: session.list_tools())
    assert fresh_db.query_one("SELECT 1 FROM users WHERE name = 'ava-mcp' AND kind = 'agent'")
    fresh_db.execute("UPDATE users SET active = 0 WHERE name = 'ava-mcp'")
    refused = _raw(_key("ava"))
    assert refused.status_code == 403 and refused.json()["detail"] == mcp_server.UNAVAILABLE
    assert "ava-mcp" not in refused.text


def test_rename_and_deactivation_carry_the_mcp_agent(fresh_db):
    from app.services import users

    users.ensure_user("ava")
    _session(_key("ava"), lambda session: session.list_tools())
    users.rename_user("ava", "avery", actor="admin")
    rows = {r["name"]: r["active"] for r in fresh_db.query("SELECT name, active FROM users")}
    assert "avery-mcp" in rows and "ava-mcp" not in rows
    users.set_active("avery", False, actor="admin")
    assert fresh_db.query_one("SELECT active FROM users WHERE name = 'avery-mcp'")["active"] == 0


def test_refusals_and_bad_arguments_are_errors_without_echo(fresh_db, monkeypatch):
    from app import mcp_server

    async def scenario(session):
        bad = await session.call_tool("list_tasks", {"limit": "not-a-number-XYZ"})
        refusal = await session.call_tool("update_task", {"task_id": 1})
        return bad, refusal

    bad, refusal = _session(_key("ava"), scenario)
    assert bad.isError and _text(bad) == mcp_server.ARGUMENTS_REFUSED
    assert "XYZ" not in _text(bad)
    assert refusal.isError and json.loads(_text(refusal))["error"]


def test_busy_and_oversized_answers_are_named(fresh_db, monkeypatch):
    import psycopg

    from app import mcp_server

    def busy(*_args, **_kwargs):
        raise psycopg.errors.LockNotAvailable()

    monkeypatch.setattr(mcp_server.briefing_svc, "my_day", busy)
    monkeypatch.setattr(mcp_server.weekly, "week_view", lambda *_a, **_k: {"x": "y" * 300_000})

    async def scenario(session):
        return _text(await session.call_tool("get_my_day", {})), _text(
            await session.call_tool("week", {})
        )

    day, week = _session(_key("ava"), scenario)
    assert json.loads(day) == {"error": mcp_server.BUSY}
    assert json.loads(week) == {"error": mcp_server.TOO_LARGE}


def test_the_context_pack_resource_masks_errors(fresh_db, monkeypatch):
    from app import mcp_server

    def boom(*_args, **_kwargs):
        raise RuntimeError("secret path")

    monkeypatch.setattr(mcp_server.context_pack, "get_pack", boom)

    async def scenario(session):
        result = await session.read_resource("skein://context-pack")
        return result.contents[0].text

    text = _session(_key("ava"), scenario)
    assert json.loads(text) == {"error": mcp_server.FAILED}


def test_a_persons_mcp_agent_is_hidden_from_other_peoples_trust_view(client, fresh_db):
    from app.services import users

    users.ensure_user("ava")
    users.ensure_user("bo")
    users.ensure_agent_identity("ava-mcp", owner="mcp")
    users.ensure_agent_identity("shared-bot", owner="mcp")
    for agent in ("ava-mcp", "shared-bot"):
        fresh_db.execute(
            "INSERT INTO pending_changes (entity, action, payload, proposed_by, requested_by,"
            " status, created_at) VALUES ('task', 'create', '{}', ?, 'ava', 'rejected', ?)",
            (agent, "2026-09-01T00:00:00+00:00"),
        )

    def seen(who: str) -> set[str]:
        reply = client.get("/api/agents/trust", headers={"X-User": who})
        return {row["agent"] for row in reply.json()}

    assert "ava-mcp" in seen("ava")
    assert "ava-mcp" not in seen("bo") and "shared-bot" in seen("bo")
