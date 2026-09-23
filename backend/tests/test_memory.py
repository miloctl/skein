"""Cross-thread agent memory: forget removes it everywhere, and the agent path is gated, capped, and carries provenance."""

import json

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
    assert any(h["entity"] == "memory" for h in search.search("rotates", reader="ava"))
    assert "rotates" in memory.memory_prompt("ava")

    response = client.delete(f"/api/memories/{m['id']}", headers={"X-User": "ava"})
    assert response.status_code == 200, response.text
    assert response.json()["deleted"] is True
    assert memory.recall(user="ava") == []
    assert memory.memory_prompt("ava") == ""
    assert [h for h in search.search("rotates", reader="ava") if h["entity"] == "memory"] == []


def test_forget_missing_memory_404_and_removal_is_logged(client):
    from app import db
    from app.services import memory

    response = client.delete("/api/memories/9999")
    assert response.status_code == 404, response.text
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


@pytest.mark.parametrize("surface", ["service", "rest"])
def test_forget_refuses_another_persons_targeted_memory(client, fresh_db, surface):
    from app import db
    from app.services import memory, search

    m = memory.remember("ZZTARGETBODYZZ", topic="ZZTARGETTOPICZZ", user="ava", actor="ava")
    before = fresh_db.query("SELECT * FROM activity ORDER BY id")
    if surface == "service":
        with pytest.raises(db.NotFound) as denied:
            memory.forget(m["id"], actor="tester")
        with pytest.raises(db.NotFound) as absent:
            memory.forget(9999, actor="tester")
        assert str(denied.value) == str(absent.value).replace("#9999", f"#{m['id']}")
    else:
        denied = client.delete(f"/api/memories/{m['id']}")
        absent = client.delete("/api/memories/9999")
        assert denied.status_code == absent.status_code == 404
        assert denied.json() == {"detail": absent.json()["detail"].replace("#9999", f"#{m['id']}")}
    assert fresh_db.query_one("SELECT id FROM memories WHERE id = ?", (m["id"],))
    assert fresh_db.query("SELECT * FROM activity ORDER BY id") == before
    assert fresh_db.query("SELECT * FROM pending_changes") == []
    # An addressed memory reaches only its addressee, on search as on recall.
    assert not any(h["entity"] == "memory" for h in search.search("ZZTARGETBODYZZ"))
    assert any(h["entity"] == "memory" for h in search.search("ZZTARGETBODYZZ", reader="ava"))
    assert memory.recall(user="tester") == []


@pytest.mark.parametrize("requester", ["", "bo"])
def test_forget_tool_refuses_targeted_content_before_proposal(fresh_db, requester):
    from app.agents import identity
    from app.services import memory
    from app.tools.memory import forget_memory

    m = memory.remember("ZZTARGETBODYZZ", topic="ZZTARGETTOPICZZ", user="ava", actor="ava")
    before = fresh_db.query("SELECT * FROM activity ORDER BY id")
    token = identity.set_requester_identity(requester)
    try:
        denied = json.loads(forget_memory(memory_id=m["id"]))
        absent = json.loads(forget_memory(memory_id=9999))
    finally:
        identity.reset_requester_identity(token)
    assert denied == {"error": absent["error"].replace("#9999", f"#{m['id']}")}
    assert fresh_db.query_one("SELECT id FROM memories WHERE id = ?", (m["id"],))
    assert fresh_db.query("SELECT * FROM activity ORDER BY id") == before
    assert fresh_db.query("SELECT * FROM pending_changes") == []
    assert fresh_db.query("SELECT * FROM notifications") == []
    assert memory.get_memory(m["id"]) is None


def test_targeted_forget_approval_uses_requester_and_preserves_agent_provenance(client, fresh_db):
    from app.agents import identity
    from app.services import memory, users
    from app.tools.memory import forget_memory

    users.ensure_user("scribe", kind="agent")
    m = memory.remember("ZZREMOVEDBODYZZ", topic="ZZREMOVEDTOPICZZ", user="ava", actor="ava")
    before = fresh_db.query_one("SELECT MAX(id) AS id FROM activity")["id"]
    agent_token = identity.set_agent_identity("scribe")
    requester_token = identity.set_requester_identity("ava")
    try:
        proposal = json.loads(forget_memory(memory_id=m["id"]))
    finally:
        identity.reset_requester_identity(requester_token)
        identity.reset_agent_identity(agent_token)
    assert proposal["status"] == "pending"
    response = client.post(
        f"/api/review/{proposal['id']}/approve", json={}, headers=_strong(client)
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
    assert not fresh_db.query_one("SELECT id FROM memories WHERE id = ?", (m["id"],))
    row = fresh_db.query_one("SELECT * FROM pending_changes WHERE id = ?", (proposal["id"],))
    assert (row["proposed_by"], row["requested_by"], row["reviewed_by"]) == (
        "scribe",
        "ava",
        "tester",
    )
    assert row["reviewed_strong"] == 1
    egress = json.dumps(
        [
            row,
            fresh_db.query("SELECT * FROM activity WHERE id > ?", (before,)),
            fresh_db.query("SELECT * FROM notifications"),
        ]
    )
    assert "ZZREMOVEDBODYZZ" not in egress and "ZZREMOVEDTOPICZZ" not in egress
    assert (
        fresh_db.query_one("SELECT actor FROM activity WHERE action = 'forget'")["actor"]
        == "scribe"
    )


def test_review_cannot_use_reviewer_as_targeted_memory_requester(fresh_db):
    from app.services import memory, review, scope, users

    users.ensure_user("scribe", kind="agent")
    users.ensure_user("ava")
    users.ensure_user("bo")
    m = memory.remember("ZZTARGETBODYZZ", user="ava", actor="ava")
    proposal = review.propose_change(
        "memory_forget", "update", {}, entity_id=m["id"], actor="scribe", requested_by="bo"
    )
    with pytest.raises(ValueError, match="auto-rejected"):
        review.approve_change(
            proposal["id"], actor="ava", strong=True, viewer=scope.Viewer("ava", True)
        )
    assert fresh_db.query_one("SELECT id FROM memories WHERE id = ?", (m["id"],))
    assert not fresh_db.query("SELECT * FROM activity WHERE action = 'forget'")
    assert "ZZTARGETBODYZZ" not in json.dumps(review.list_changes(status="rejected"))


def test_forget_proposal_cannot_supply_requester_in_payload(fresh_db):
    from app.services import memory, review

    m = memory.remember("ZZTARGETBODYZZ", user="ava", actor="ava")
    with pytest.raises(ValueError, match="requester"):
        review.propose_change(
            "memory_forget", "update", {"requester": "ava"}, entity_id=m["id"], actor="agent"
        )
    assert fresh_db.query("SELECT * FROM pending_changes") == []


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
    from app.services import delegation, memory, users, wording
    from app.tools.memory import remember as remember_tool

    users.ensure_user("scribe", kind="agent")
    users.ensure_user("mira")
    delegation.set_authority("scribe", "memory", "forbidden", actor="mira")
    token = set_agent_identity("scribe")
    try:
        out = j.loads(remember_tool(content="steering text"))
        assert out == {"error": wording.write_policy_denied()}
    finally:
        reset_agent_identity(token)
    assert fresh_db.query_one("SELECT id FROM memories WHERE content = 'steering text'") is None
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
    with pytest.raises(ValueError, match="The limit for memory is 10 per minute"):
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


def test_recall_finds_a_memory_behind_many_matching_tasks(fresh_db):
    """recall kept memory rows only AFTER taking the top hits across every
    entity, so 25 tasks with the same word left no room for the memory."""
    from app.services import memory, work

    # two mentions each: every task outranks the memory's one
    for i in range(25):
        work.create_task(f"vendor contract step {i}: vendor contract")
    m = memory.remember("we noted in the review that the vendor contract renews in March")
    assert [r["id"] for r in memory.recall("vendor contract")] == [m["id"]]


def test_the_memory_list_reaches_past_the_ten_newest(client, fresh_db):
    """/api/memories is the only surface that lists memories and offers to
    delete one; capped at 10, older memories kept steering conversations from
    a row no person could reach."""
    from app import db

    # inserted directly: remember() is rate-capped at 10 a minute per person
    for i in range(12):
        fresh_db.execute(
            "INSERT INTO memories (content, created_at) VALUES (?, ?)", (f"fact {i}", db.now())
        )
    assert len(client.get("/api/memories").json()) == 12


def test_an_addressed_memory_reaches_only_its_addressee_on_every_search(client, fresh_db):
    """`/remember` and the MCP tool address each memory to the speaker at the
    workspace tier, and no write path offers another tier. Search, /ask, the
    short-id door and the agent search tool served those memories to every
    teammate, while the memory list and recall showed them to the addressee
    alone."""
    from app.agents import identity
    from app.services import memory
    from app.tools.platform import search_workspace

    mine = memory.remember("ZZADDRESSEDZZ appointment", user="ava", actor="ava")["id"]
    team = memory.remember("ZZADDRESSEDZZ freeze", actor="ava")["id"]

    def ids(hits):
        return {h["entity_id"] for h in hits if h["entity"] == "memory"}

    for who, expected in (("bo", {team}), ("ava", {mine, team})):
        headers = {"X-User": who}
        found = client.get("/api/search", params={"q": "ZZADDRESSEDZZ"}, headers=headers)
        assert ids(found.json()) == expected, who
        cited = client.get("/api/ask", params={"q": "ZZADDRESSEDZZ"}, headers=headers)
        refs = {c["ref"] for c in cited.json()["citations"]}
        assert {r for r in refs if r.startswith("memory")} == {f"memory #{i}" for i in expected}
        direct = client.get("/api/search", params={"q": f"memory {mine}"}, headers=headers)
        assert (mine in ids(direct.json())) is (who == "ava"), who

    for requester, expected in (("bo", {team}), ("ava", {mine, team}), ("", {team})):
        token = identity.set_requester_identity(requester)
        try:
            assert ids(json.loads(search_workspace(query="ZZADDRESSEDZZ"))) == expected
        finally:
            identity.reset_requester_identity(token)
    assert {m["id"] for m in memory.recall("ZZADDRESSEDZZ", user="ava")} == {mine, team}


def test_a_private_memory_reaches_its_addressee_and_not_the_database_role(fresh_db):
    """memories' author column is `user`, which unquoted is CURRENT_USER: the
    tier check compared the database role name, so the addressee never read
    their own private memory and a person named like the role read all."""
    from app.services import memory, scope, users

    role = fresh_db.query_one("SELECT current_user AS name")["name"]
    users.ensure_user("ava")
    mid = memory.remember("ZZPRIVATEZZ note", user="ava", actor="ava", visibility=scope.PRIVATE)[
        "id"
    ]
    ava = scope.Viewer("ava", True)
    assert [m["id"] for m in memory.recall(user="ava", viewer=ava)] == [mid]
    stranger = scope.Viewer(role, True)
    assert memory.recall(user=role, viewer=stranger) == []
