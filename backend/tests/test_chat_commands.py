"""Slash commands: deterministic dispatch shared by chat, Slack, and mock."""

from app.agents import commands


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
    assert "private" in out
    assert calls == []


def test_bridge_skipped_while_agent_turn_in_flight(client, monkeypatch):
    from app.routes import chat as chat_route

    calls = []
    monkeypatch.setattr("app.agents.session_log.log_exchange", lambda *a: calls.append(a))
    chat_route._inflight["t"] += 1
    try:
        _read_chat(client, "/help")
    finally:
        del chat_route._inflight["t"]
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
        maximum_core_exclusive="0.3.0",
        policies=(PolicyContribution("atlas.workplace.commands", deny_commands),),
    )
    from fastapi.testclient import TestClient

    with TestClient(create_app(modules=(module,)), headers={"X-User": "mira"}) as governed:
        remembered = _read_chat(governed, "/remember do not store this")
        planned = _read_chat(governed, "/plan prototype blocked plan")
        assert "policy denied" in remembered.lower()
        assert "policy denied" in planned.lower()
        assert governed.get("/api/memories").json() == []
        assert all(row["name"] != "blocked plan" for row in governed.get("/api/engagements").json())
