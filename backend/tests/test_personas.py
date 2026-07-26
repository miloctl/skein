"""The Bench: persona registry, /as invocation, per-persona identity."""

from app.agents import commands
from app.agents.identity import agent_identity, reset_agent_identity, set_agent_identity
from app.services import personas


def _read_chat(client, message):
    with client.stream("POST", "/api/chat", json={"thread_id": "p", "message": message}) as resp:
        assert resp.status_code == 200
        return resp.read().decode()


def test_bench_loads_ten_personas():
    roster = personas.list_personas()
    assert len(roster) == 10
    slugs = {p["slug"] for p in roster}
    assert {"code-reviewer", "growth-mentor", "training-designer"} <= slugs
    assert all(p["name"] and p["description"] and p["emoji"] for p in roster)


def test_get_persona_body_and_unknown():
    p = personas.get_persona("code-reviewer")
    assert "review" in p["body"].lower()
    try:
        personas.get_persona("nope")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "code-reviewer" in str(exc)


def test_personas_rest(client):
    roster = client.get("/api/personas").json()
    assert len(roster) == 10
    assert "body" not in roster[0]
    one = client.get("/api/personas/growth-mentor").json()
    assert one["body"]
    assert client.get("/api/personas/nope").status_code == 400


def test_personas_command_lists_bench(client):
    out = _read_chat(client, "/personas")
    assert "The bench" in out
    assert "growth-mentor" in out and "/as" in out


def test_as_masthead_and_routing(client, fresh_db):
    out = _read_chat(client, "/as code-reviewer todo: refactor the gate")
    assert "Code Reviewer" in out  # masthead
    tasks = client.get("/api/tasks").json()
    assert any("refactor the gate" in t["title"] for t in tasks)
    # invocation registered the persona as an agent identity
    row = fresh_db.query_one("SELECT * FROM users WHERE name = 'code-reviewer'")
    assert row and row["kind"] == "agent"


def test_as_usage_and_unknown_are_deterministic(client):
    assert "Usage" in _read_chat(client, "/as")
    assert "Usage" in _read_chat(client, "/as code-reviewer")
    out = _read_chat(client, "/as ghost do things")
    assert "no persona 'ghost'" in out


def test_catalog_includes_bench_commands(client):
    names = [c["name"] for c in client.get("/api/chat/commands").json()]
    assert "personas" in names and "as" in names


def test_identity_contextvar_signs_proposals(fresh_db, monkeypatch):
    from app import config
    from app.tools._gate import gated_write

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    token = set_agent_identity("code-reviewer")
    try:
        assert agent_identity() == "code-reviewer"
        gated_write("task", "create", {"title": "signed"}, direct=lambda: {"id": 0})
    finally:
        reset_agent_identity(token)
    assert agent_identity() == "agent"
    row = fresh_db.query_one("SELECT * FROM pending_changes ORDER BY id DESC")
    assert row["proposed_by"] == "code-reviewer"


def test_dispatch_passes_as_through_to_route():
    assert commands.dispatch("/as code-reviewer hello", "tester") is None
