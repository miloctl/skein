"""Slash commands: deterministic dispatch shared by chat and mock."""

from app.agents import commands
from app.services import wording


def _read_chat(client, message):
    with client.stream("POST", "/api/chat", json={"thread_id": "t", "message": message}) as resp:
        assert resp.status_code == 200
        return resp.read().decode()


def test_catalog_endpoint_matches_registry(client):
    rows = client.get("/api/chat/commands").json()
    assert [r["name"] for r in rows] == [c["name"] for c in commands.COMMANDS]
    assert all(set(r) == {"name", "args", "description"} for r in rows)


def test_dispatch_freeform_is_none():
    assert commands.dispatch("ship the API", "tester") is None
    assert commands.dispatch("what is /help?", "tester") is None


def test_dispatch_non_command_slash_falls_through():
    assert commands.dispatch("/etc/hosts is broken", "tester") is None
    assert commands.dispatch("/2fa rollout", "tester") is None


def test_unknown_command_gets_suggestion(client):
    out = _read_chat(client, "/hlp")
    assert "is not a command" in out
    assert "/help" in out


def test_command_case_insensitive(client):
    assert "Command" in _read_chat(client, "/HELP")


def test_search_requires_query(client):
    assert "Usage: `/search <query>`" in _read_chat(client, "/search")


def test_plan_requires_slug_and_name(client):
    assert "Usage: `/plan" in _read_chat(client, "/plan incident")


def test_remember_roundtrip(client):
    out = _read_chat(client, "/remember demos every Friday")
    assert "Remembered" in out
    memories = client.get("/api/memories").json()
    assert any("Friday" in m["content"] for m in memories)


def test_briefing_streams_tool_event(client):
    out = _read_chat(client, "/briefing")
    assert '"type": "tool"' in out
    assert "My Day" in out


def test_briefing_separates_personal_reviews_from_the_team_queue(client):
    from app.services import review

    review.propose_change(
        "note",
        "create",
        {"topic": "mine", "content": "x"},
        actor="agent",
        requested_by="tester",
    )
    review.propose_change("note", "create", {"topic": "shared", "content": "x"}, actor="agent")

    out = _read_chat(client, "/briefing")
    assert "Reviews waiting on you: 1" in out
    assert "Team queue \\u2014 pending reviews: 1" in out


def test_help_lists_every_command(client):
    out = _read_chat(client, "/help")
    for c in commands.COMMANDS:
        assert f"/{c['name']}" in out


def test_command_stream_bridges_exchange(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.agents.session_log.log_exchange", lambda t, u, a: calls.append((t, u, a))
    )
    _read_chat(client, "/briefing")
    assert len(calls) == 1
    thread, user_text, assistant_text = calls[0]
    assert (thread, user_text) == ("t", "/briefing")
    # the model copy carries the content but not the 🔧 chip markup
    assert "My Day" in assistant_text and "🔧" not in assistant_text


def test_command_wrapped_fb_refused_before_bridge(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.agents.session_log.log_exchange", lambda *a: calls.append(a))
    out = _read_chat(client, "/remember fb: dana — struggling with the client")
    assert wording.private_feedback_agent_refusal() in out
    assert "struggling with the client" not in out
    assert calls == []


def test_bridge_skipped_while_agent_turn_in_flight(client, monkeypatch):
    from app.services import chat_threads

    calls = []
    monkeypatch.setattr("app.agents.session_log.log_exchange", lambda *a: calls.append(a))
    from app import db

    # what a turn on any process holds while it streams
    token = db.claim_job("chat-turn:t", "turn", lease_seconds=60)
    assert token
    try:
        assert chat_threads.model_turn_active("t")
        _read_chat(client, "/help")
    finally:
        db.release_job("chat-turn:t", "turn", token)
    assert calls == []


def test_a_late_receipt_in_a_command_survives_the_stream(client, monkeypatch):
    """A receipt recorded after a command generator's last yield must reach
    the stream and the transcript — the post-loop drain mirrors pump()'s, and
    without it the receipt vanishes from all three destinations."""
    from app.agents import commands, receipts

    async def late_receipt_command():
        yield {"data": "working…"}
        receipts.record("wrote", "note", "recorded after the last yield", 5)

    monkeypatch.setattr(
        commands, "dispatch", lambda text, user, viewer=None, access=None: late_receipt_command()
    )
    body = client.post("/api/chat", json={"thread_id": "t-late", "message": "/briefing"}).text
    assert '"kind": "wrote"' in body
    assert "recorded after the last yield" in body


def test_help_names_the_right_reason_for_mock(monkeypatch):
    """Mock is reached two ways, and only one is about configuration being
    absent: a degraded provider claiming "no API key configured" sends the
    operator to fix the wrong thing (found live on a bad SKEIN_MAX_TOKENS)."""
    from app import config
    from app.agents import commands

    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "mock")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    assert "no model provider configured" in commands.help_text()

    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "SKEIN_MAX_TOKENS is not a number")
    out = commands.help_text()
    assert "unavailable" in out
    assert "no API key" not in out


def test_deterministic_writes_use_the_composed_workplace_policy(fresh_db):
    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.main import create_app

    def deny_commands(request):
        if request.action in ("playbook.create", "memory.create"):
            return PolicyDecision(PolicyEffect.DENY, ("Commands are paused.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        policies=(PolicyContribution("atlas.workplace.commands", deny_commands),),
    )
    from fastapi.testclient import TestClient

    with TestClient(create_app(modules=(module,)), headers={"X-User": "mira"}) as governed:
        remembered = _read_chat(governed, "/remember do not store this")
        planned = _read_chat(governed, "/plan prototype blocked plan")
        expected = (
            "Workplace policy denied this action. Use an allowed action or ask an"
            " administrator to change the policy."
        )
        assert expected in remembered
        assert expected in planned
        assert "⚠" not in remembered + planned
        assert governed.get("/api/memories").json() == []
        assert all(row["name"] != "blocked plan" for row in governed.get("/api/engagements").json())


def test_remember_in_a_linked_thread_stays_personal_until_shared(client):
    """A plain /remember in a linked thread filed the engagement proposal,
    whose team notice quoted the speaker's words. It is the speaker's own
    memory, recalled with that engagement. `team:` shares it, with the same
    strong-identity bar as POST /api/engagements/{id}/memory."""
    from conftest import _strong

    from app import db
    from app.services import engagements, memory, scope, users

    users.ensure_user("bo")  # a second person to approve the shared one
    eid = engagements.create_engagement("Atlas", actor="tester")["id"]
    # claim the thread, then link it — the same order the UI produces
    _read_chat(client, "hello")
    client.patch("/api/chats/t", json={"engagement_id": eid})

    out = _read_chat(client, "/remember ZZTIREDZZ this week")
    assert "Remembered" in out
    assert db.query("SELECT id FROM pending_changes") == []
    assert db.query("SELECT id FROM notifications WHERE message LIKE '%ZZTIREDZZ%'") == []
    tester = scope.Viewer("tester", True)
    recalled = memory.recall(user="tester", viewer=tester, engagement_id=eid)
    assert [m["content"] for m in recalled] == ["ZZTIREDZZ this week"]

    shared = "/remember team: the client reads Thursday demos"
    assert "requires strong identity" in _read_chat(client, shared)
    body = {"thread_id": "t", "message": shared}
    with client.stream("POST", "/api/chat", json=body, headers=_strong(client)) as resp:
        out = resp.read().decode()
    assert "proposal" in out and "Approvals" in out
    row = db.query_one(
        "SELECT payload FROM pending_changes WHERE entity = 'memory' AND status = 'pending'"
    )
    assert row is not None and "Thursday demos" in row["payload"]
    assert "team:" not in row["payload"]


def test_remember_in_a_linked_thread_decides_on_the_engagement(fresh_db):
    """/remember asked the policy about an unlinked workspace memory, so a
    rule that refuses memories on a regulated engagement passed from chat
    while POST /api/engagements/{id}/memory refused."""
    from fastapi.testclient import TestClient

    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.main import create_app
    from app.services import engagements

    def regulated_memories(request):
        if request.action == "memory.create" and request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY, ("Regulated work keeps no memories.",))
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        policies=(PolicyContribution("atlas.workplace.memories", regulated_memories),),
    )
    eid = engagements.create_engagement("Ledger", "regulated", actor="mira")["id"]
    with TestClient(create_app(modules=(module,)), headers={"X-User": "mira"}) as governed:
        _read_chat(governed, "hello")
        governed.patch("/api/chats/t", json={"engagement_id": eid})
        out = _read_chat(governed, "/remember the client audit is Tuesday")
        assert "Workplace policy denied this action." in out
        assert governed.get("/api/memories").json() == []


def test_remember_in_an_unlinked_thread_stays_a_direct_team_memory(client):
    out = _read_chat(client, "/remember demos every Friday")
    assert "Remembered" in out


def _menu(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setattr(config, "EFFECTIVE_PROVIDER", "openai_compatible")
    monkeypatch.setattr(config, "MODEL_PROVIDER_ERROR", "")
    monkeypatch.setattr(config, "MODEL_ID", "team-default")
    monkeypatch.setattr(config, "MODELS_ERROR", "")
    entry = {
        "label": "",
        "detail": "",
        "max_tokens": None,
        "context_tokens": None,
        "price": None,
        "params": {},
        "attachments": None,
    }
    monkeypatch.setattr(
        config,
        "MODELS",
        {
            "team-default": {**entry, "id": "team-default"},
            "mini": {**entry, "id": "mini", "label": "mini", "detail": "cheap and quick"},
        },
    )


def test_model_command_names_the_mock_provider(client, fresh_db):
    assert "mock provider" in _read_chat(client, "/model")


def test_model_command_lists_the_menu_and_picks_per_chat(client, fresh_db, monkeypatch):
    from app.services import chat_threads

    _menu(monkeypatch)
    out = _read_chat(client, "/model")
    assert "team model, **team-default**" in out
    assert "mini" in out and "cheap and quick" in out
    out = _read_chat(client, "/model mini")
    assert "now uses **mini**" in out
    assert chat_threads.thread_model("t") == "mini"
    # per chat: another thread stays on the team model
    assert chat_threads.thread_model("other") == ""
    assert "**mini** (picked here)" in _read_chat(client, "/model")
    assert "team model" in _read_chat(client, "/model default")
    assert chat_threads.thread_model("t") == ""


def test_model_command_refuses_an_unknown_id_without_echoing_it(client, fresh_db, monkeypatch):
    from app.services import chat_threads

    _menu(monkeypatch)
    out = _read_chat(client, "/model gpt-nope")
    assert "not changed" in out and "expected one of" in out
    assert "gpt-nope" not in out
    assert chat_threads.thread_model("t") == ""


def test_model_command_needs_a_chat(fresh_db):
    import asyncio

    async def first():
        stream = commands.dispatch("/model", "tester")
        assert stream is not None
        async for event in stream:
            if "data" in event:
                return event["data"]
        return ""

    assert "inside a chat" in asyncio.run(first())


def test_the_chat_pick_outranks_a_persona_model(client, fresh_db, monkeypatch):
    from app.agents import team_agent
    from app.services import chat_threads, personas

    _menu(monkeypatch)
    chat_threads.claim_thread("t", "tester")
    chat_threads.set_thread_model("t", "tester", "mini")
    real = personas.behavior

    def with_model(slug):
        return {**real(slug), "model": "team-default"}

    monkeypatch.setattr("app.routes.chat.personas.behavior", with_model)
    seen: dict = {}

    class Quiet:
        async def stream_async(self, message):
            yield {"data": "ok"}

    def build(*_args, **kwargs):
        seen.update(kwargs)
        return Quiet()

    monkeypatch.setattr(team_agent, "build_agent", build)
    monkeypatch.setattr("app.routes.chat.build_agent", build)
    _read_chat(client, "/as growth-mentor hello")
    assert seen["resolved_model"] == "mini"


def test_a_pick_that_left_the_menu_is_reported_not_guessed(client, fresh_db, monkeypatch):
    from app import config
    from app.services import chat_threads

    _menu(monkeypatch)
    chat_threads.claim_thread("t", "tester")
    chat_threads.set_thread_model("t", "tester", "mini")
    monkeypatch.setattr(config, "MODELS", {"team-default": config.MODELS["team-default"]})
    assert chat_threads.thread_model("t") == ""
    out = _read_chat(client, "/model")
    assert "team model" in out
    assert "no longer in the menu" in out


def test_the_chat_pick_reaches_the_agent_build(client, fresh_db, monkeypatch):
    """The pick outranks the persona default and the team pick: it is the one
    explicit choice in the ladder (routes/chat.py resolved_model)."""
    from app.services import chat_threads

    _menu(monkeypatch)
    chat_threads.claim_thread("t", "tester")
    chat_threads.set_thread_model("t", "tester", "mini")
    seen: dict = {}

    class Quiet:
        async def stream_async(self, message):
            yield {"data": "ok"}

    def build(*_args, **kwargs):
        seen.update(kwargs)
        return Quiet()

    monkeypatch.setattr("app.routes.chat.build_agent", build)
    _read_chat(client, "hello")
    assert seen["resolved_model"] == "mini"


def test_a_failing_command_never_shows_the_raw_server_error(client, monkeypatch):
    """A database error's text names the host, the port, and the role."""
    import psycopg

    entry = next(c for c in commands.COMMANDS if c["name"] == "help")

    async def broken(*_args, **_kwargs):
        raise psycopg.OperationalError(
            "connection to server at 10.0.0.5 port 5432 failed: role skein"
        )
        yield {}  # pragma: no cover — makes this an async generator

    monkeypatch.setitem(entry, "handler", broken)
    out = _read_chat(client, "/help")
    assert '"type": "error"' in out
    assert "10.0.0.5" not in out and "skein" not in out.split('"error"', 1)[1]


def test_a_linked_team_memory_refusal_names_a_route_that_works(client):
    """In a linked chat, the refusal said to "remember the fact for the team",
    and /remember team: there files for the engagement again."""
    from conftest import _strong

    from app.services import engagements, scope

    eid = engagements.create_engagement("Solo", actor="tester", visibility=scope.PRIVATE)["id"]
    _read_chat(client, "hello")
    client.patch("/api/chats/t", json={"engagement_id": eid})
    body = {"thread_id": "t", "message": "/remember team: the vendor is late"}
    with client.stream("POST", "/api/chat", json=body, headers=_strong(client)) as resp:
        out = resp.read().decode()
    assert "send /remember without team:" in out
