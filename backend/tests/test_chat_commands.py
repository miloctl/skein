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

    monkeypatch.setattr(commands, "dispatch", lambda text, user: late_receipt_command())
    body = client.post("/api/chat", json={"thread_id": "t-late", "message": "/briefing"}).text
    assert '"kind": "wrote"' in body
    assert "recorded after the last yield" in body
