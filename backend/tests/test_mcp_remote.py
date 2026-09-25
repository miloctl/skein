"""Skein's MCP server over HTTP: the caller is resolved per request, acts
as their own `<name>-mcp` agent, a weak or agent identity is refused before
the MCP layer, tool bodies run off the event loop with the request's
context, and an unexpected error never leaks its text."""

import asyncio
import json

import httpx2
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
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as http,
            streamable_http_client(URL, http_client=http) as streams,
            ClientSession(streams[0], streams[1]) as session,
        ):
            await session.initialize()
            return await scenario(session)

    return asyncio.run(run())


def _raw(headers: dict, method: str = "initialize", params: dict | None = None) -> httpx2.Response:
    from app.main import app

    async def run():
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://testserver",
                headers=headers,
            ) as http,
        ):
            return await http.post(
                URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": method,
                    "params": params
                    if params is not None
                    else {
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


def test_stdio_process_discovers_calls_and_reads_resources(fresh_db, tmp_path):
    import sys
    from pathlib import Path

    import anyio
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client

    from app import config, mcp_server
    from app.services import users, work

    users.ensure_user("ava")
    task = work.create_task("Read this task over stdio", actor="ava")
    backend = Path(mcp_server.__file__).resolve().parents[1]
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        cwd=backend,
        env={
            "PYTHON_DOTENV_DISABLED": "1",
            "PYTHONPATH": str(backend),
            "SKEIN_DATABASE_URL": config.DATABASE_URL,
            "SKEIN_DATA_DIR": str(tmp_path),
            "SKEIN_MODEL_PROVIDER": "mock",
            "SKEIN_SCHEDULER": "0",
            "SKEIN_MCP_USER": "mcp-agent",
        },
    )

    async def run():
        # A failed handshake must still leave stdio_client's shielded,
        # bounded shutdown in charge of reaping the real child process.
        with anyio.fail_after(30):
            with (tmp_path / "stdio-stderr.log").open("w") as stderr:
                async with (
                    stdio_client(server, errlog=stderr) as streams,
                    ClientSession(*streams, read_timeout_seconds=10) as session,
                ):
                    initialized = await session.initialize()
                    assert initialized.server_info.name == "skein"
                    tools = (await session.list_tools()).tools
                    assert len(tools) == 24
                    result = await session.call_tool("list_tasks", {})
                    assert not result.is_error, _text(result)
                    assert [row["id"] for row in json.loads(_text(result))] == [task["id"]]
                    bad = await session.call_tool("list_tasks", {"limit": "private-XYZ"})
                    assert bad.is_error and _text(bad) == mcp_server.ARGUMENTS_REFUSED
                    assert "XYZ" not in _text(bad)
                    resources = (await session.list_resources()).resources
                    assert "skein://context-pack" in {resource.uri for resource in resources}
                    resource = await session.read_resource("skein://context-pack")
                    assert "# Team context pack" in resource.contents[0].text

    asyncio.run(run())


def test_tools_are_listed_with_annotations(fresh_db):
    async def scenario(session):
        return (await session.list_tools()).tools

    tools = _session(_key("ava"), scenario)
    by_name = {tool.name: tool for tool in tools}
    assert len(tools) == 24
    assert by_name["get_my_day"].annotations.read_only_hint is True
    assert by_name["capture"].annotations.read_only_hint is False
    assert by_name["capture"].annotations.destructive_hint is False
    assert by_name["complete_task"].annotations.idempotent_hint is True
    assert set(by_name["list_tasks"].input_schema["properties"]) >= {"limit", "offset"}
    assert {"update_task", "ask_question", "answer_question", "resolve_blocker", "week"} <= set(
        by_name
    )


def test_discovery_keeps_protocol_wire_aliases(fresh_db):
    response = _raw(_key("ava"), "tools/list", {})
    assert response.status_code == 200, response.text
    tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    assert tools["get_my_day"]["annotations"]["readOnlyHint"] is True
    assert tools["capture"]["annotations"]["readOnlyHint"] is False
    assert tools["capture"]["annotations"]["destructiveHint"] is False
    assert tools["complete_task"]["annotations"]["idempotentHint"] is True
    assert set(tools["list_tasks"]["inputSchema"]["properties"]) >= {"limit", "offset"}
    assert "input_schema" not in tools["list_tasks"]
    assert "read_only_hint" not in tools["get_my_day"]["annotations"]


@pytest.mark.parametrize("chunked", [False, True])
def test_oversized_request_is_json_without_echo(fresh_db, chunked):
    from app.main import app
    from app.mcp_server import BODY_MAX_BYTES

    headers = _key("ava")
    body = b"private-body-XYZ" + b" " * BODY_MAX_BYTES

    async def chunks():
        for offset in range(0, len(body), 65536):
            yield body[offset : offset + 65536]

    async def run():
        async with (
            app.router.lifespan_context(app),
            httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=headers) as http,
        ):
            request = http.build_request(
                "POST",
                URL,
                content=chunks() if chunked else body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert ("content-length" not in request.headers) is chunked
            return await http.send(request)

    response = asyncio.run(run())
    assert response.status_code == 413, response.text
    assert response.json() == {"detail": "The request body is too large."}
    assert "XYZ" not in response.text


@pytest.mark.parametrize("origin", ["http://localhost:3000", "https://untrusted.example"])
def test_mounted_host_and_origin_keep_api_policy(fresh_db, origin):
    # Bearer-authenticated MCP does not acquire the standalone SDK's loopback
    # allowlist. Disallowed browser origins still get no CORS permission.
    response = _raw({**_key("ava"), "Host": "skein.internal.example", "Origin": origin})
    assert response.status_code == 200, response.text
    assert "serverInfo" in response.json()["result"]
    if origin == "https://untrusted.example":
        assert "access-control-allow-origin" not in response.headers


def test_concurrent_writes_keep_request_context(fresh_db, monkeypatch):
    import threading

    from app import config, mcp_server
    from app.main import app
    from app.services import users

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    for person in ("ava", "bo"):
        users.ensure_user(person)
    headers = {person: _key(person) for person in ("ava", "bo")}
    barrier = threading.Barrier(2, timeout=10)
    plan = mcp_server.capture_svc.plan

    def overlapping_plan(*args, **kwargs):
        barrier.wait()
        return plan(*args, **kwargs)

    monkeypatch.setattr(mcp_server.capture_svc, "plan", overlapping_plan)

    async def run():
        async with app.router.lifespan_context(app):

            async def call(person):
                async with (
                    httpx2.AsyncClient(
                        transport=httpx2.ASGITransport(app=app), headers=headers[person]
                    ) as http,
                    streamable_http_client(URL, http_client=http) as streams,
                    ClientSession(streams[0], streams[1]) as session,
                ):
                    await session.initialize()
                    result = await session.call_tool(
                        "capture", {"text": f"todo: work for {person}"}
                    )
                    assert not result.is_error, _text(result)

            await asyncio.gather(call("ava"), call("bo"))

    asyncio.run(run())
    rows = fresh_db.query(
        "SELECT proposed_by, requested_by FROM pending_changes ORDER BY requested_by"
    )
    assert [(row["proposed_by"], row["requested_by"]) for row in rows] == [
        ("ava-mcp", "ava"),
        ("bo-mcp", "bo"),
    ]
    assert mcp_server._actor() == mcp_server.ACTOR
    assert mcp_server.requester_identity() == ""


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


@pytest.mark.parametrize(
    ("tool", "fields"),
    [
        ("create_task", {"priority": "critical"}),
        ("create_task", {"priority": ""}),
        ("update_task", {"priority": "critical"}),
        ("update_task", {"status": "finished"}),
        ("update_task", {"due_date": "tomorrow"}),
        ("update_task", {"waiting_on": "unknown:12"}),
    ],
)
def test_invalid_task_fields_are_tool_errors_without_pending_proposals(
    fresh_db, monkeypatch, tool, fields
):
    from app import config
    from app.services import users, work

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("ava")
    task = work.create_task("Existing task", actor="ava")
    arguments = {"title": "Proposed task"} if tool == "create_task" else {"task_id": task["id"]}

    async def scenario(session):
        return await session.call_tool(tool, {**arguments, **fields})

    result = _session(_key("ava"), scenario)
    assert result.is_error, _text(result)
    assert "error" in json.loads(_text(result))
    assert fresh_db.query("SELECT id FROM pending_changes") == []
    assert fresh_db.query("SELECT id FROM activity WHERE action = 'propose_change'") == []


@pytest.mark.parametrize("reference", ["task:{id}", "task:#{id}", "task:00{id}", "task: #{id} "])
def test_task_self_wait_is_refused_before_mcp_or_proposal_storage(fresh_db, monkeypatch, reference):
    from app import config
    from app.services import review, users, work

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("ava")
    task_id = work.create_task("Existing task", actor="ava")["id"]
    payload = {"waiting_on": reference.format(id=task_id)}
    with pytest.raises(ValueError, match="cannot wait on itself"):
        work.update_task(task_id, **payload, actor="ava")

    async def scenario(session):
        return await session.call_tool("update_task", {"task_id": task_id, **payload})

    result = _session(_key("ava"), scenario)
    assert result.is_error, _text(result)
    assert "cannot wait on itself" in json.loads(_text(result))["error"]
    with pytest.raises(ValueError, match="cannot wait on itself"):
        review.propose_change("task", "update", payload, entity_id=task_id, actor="agent")
    assert fresh_db.query("SELECT id FROM pending_changes") == []
    assert fresh_db.query("SELECT id FROM activity WHERE action = 'propose_change'") == []


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
    assert bad.is_error and _text(bad) == mcp_server.ARGUMENTS_REFUSED
    assert "XYZ" not in _text(bad)
    assert refusal.is_error and json.loads(_text(refusal))["error"]
    assert refusal.structured_content is None


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
