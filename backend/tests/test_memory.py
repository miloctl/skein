"""Cross-thread agent memory: forget removes it everywhere, and the agent path is gated, capped, and carries provenance."""

import pytest
from conftest import _strong


def _approve_latest(client):
    from app.services.api_keys import create_key

    headers = {"Authorization": f"Bearer {create_key('tester', 'p')['key']}"}
    pending = client.get("/api/review?status=pending").json()
    assert pending, "expected a pending proposal"
    r = client.post(f"/api/review/{pending[0]['id']}/approve", json={}, headers=headers)
    assert r.json()["status"] == "approved"
    return pending[0]


def test_forget_removes_memory_everywhere(client):
    from app.services import memory, search

    m = memory.remember("the staging DB password rotates on tuesdays", topic="ops", user="ava")
    assert any(h["entity"] == "memory" for h in search.search("rotates"))
    assert "rotates" in memory.memory_prompt("ava")

    out = client.delete(f"/api/memories/{m['id']}").json()
    assert out["deleted"] is True
    assert memory.recall(user="ava") == []
    assert memory.memory_prompt("ava") == ""
    assert [h for h in search.search("rotates") if h["entity"] == "memory"] == []


def test_forget_missing_memory_404_and_removal_is_logged(client):
    from app import db
    from app.services import memory

    assert client.delete("/api/memories/9999").status_code == 404
    m = memory.remember("wrong fact", topic="bad")
    client.delete(f"/api/memories/{m['id']}")
    logged = db.query("SELECT * FROM activity WHERE action = 'forget'")
    assert logged and f"#{m['id']}" in logged[0]["detail"]
    assert logged[0]["actor"] == "tester"


def test_agent_forget_memory_gated_and_applies(client, fresh_db, monkeypatch):
    from app import config
    from app.services import memory
    from app.tools.memory import forget_memory

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    m = memory.remember("the cluster password rotates on Fridays", topic="ops", actor="agent")
    out = forget_memory(memory_id=m["id"])
    assert "pending" in out
    assert fresh_db.query_one("SELECT id FROM memories WHERE id = ?", (m["id"],))
    _approve_latest(client)
    assert not fresh_db.query_one("SELECT id FROM memories WHERE id = ?", (m["id"],))


def test_agent_remember_is_gated_and_carries_provenance(client, fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import users
    from app.tools.memory import remember as remember_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scribe", kind="agent")
    token = set_agent_identity("scribe")
    try:
        out = j.loads(remember_tool(content="the deploy window is Fridays", topic="ops"))
        assert out.get("note") == "queued for human review"
        pid = out["id"]
    finally:
        reset_agent_identity(token)
    r = client.post(f"/api/review/{pid}/approve", json={}, headers=_strong(client))
    row = fresh_db.query_one(
        "SELECT origin, created_by FROM memories WHERE id = ?", (r.json()["result"]["id"],)
    )
    assert row["origin"] == "agent_verified" and row["created_by"] == "scribe"


def test_agent_remember_respects_forbidden_and_caps(fresh_db):
    import json as j

    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import delegation, memory, users
    from app.tools.memory import remember as remember_tool

    users.ensure_user("scribe", kind="agent")
    users.ensure_user("mira")
    delegation.set_authority("scribe", "memory", "forbidden", actor="mira")
    token = set_agent_identity("scribe")
    try:
        out = j.loads(remember_tool(content="steering text"))
        assert "forbidden" in out["error"]
    finally:
        reset_agent_identity(token)
    with pytest.raises(ValueError, match="2000"):
        memory.remember("x" * 2001, actor="mira")


def test_mcp_remember_routes_through_the_gate(client, fresh_db, monkeypatch):
    import json as j

    from app import config, mcp_server
    from app.services import users

    users.ensure_user("mcp-agent", kind="agent")
    monkeypatch.setattr(mcp_server, "ACTOR", "mcp-agent")
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    out = j.loads(mcp_server.remember("the deploy window is Fridays", topic="ops"))
    assert out.get("note") == "queued for human review"
    pending = client.get("/api/review?status=pending").json()
    assert any(p["entity"] == "memory" and p["proposed_by"] == "mcp-agent" for p in pending)
    big = j.loads(mcp_server.remember("x" * 2001))
    assert "2000" in big["error"]


def test_memory_rate_cap_and_human_provenance(fresh_db):
    from app.services import memory

    for i in range(10):
        memory.remember(f"fact {i}", actor="mira")
    with pytest.raises(ValueError, match="capped at 10/minute"):
        memory.remember("fact 11", actor="mira")
    row = fresh_db.query_one("SELECT origin, created_by FROM memories WHERE id = 1")
    assert row["origin"] == "human" and row["created_by"] == "mira"


def test_oversized_memory_fails_on_the_agent_not_the_reviewer(fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import users
    from app.tools.memory import remember as remember_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scribe", kind="agent")
    token = set_agent_identity("scribe")
    try:
        out = j.loads(remember_tool(content="x" * 2001))
    finally:
        reset_agent_identity(token)
    assert "2000" in out["error"]
    assert not fresh_db.query_one(
        "SELECT id FROM pending_changes WHERE entity = 'memory' AND status = 'pending'"
    )
