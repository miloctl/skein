"""Cross-thread agent memory: forget removes it everywhere, and the agent path is gated, capped, and carries provenance."""

import json
from pathlib import Path

import pytest
from conftest import _strong, _turn


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
    try:
        with _turn("ava"):
            proposal = json.loads(forget_memory(memory_id=m["id"]))
    finally:
        identity.reset_agent_identity(agent_token)
    assert proposal["status"] == "pending"
    # only the addressee reads or judges a proposal about her memory
    response = client.post(
        f"/api/review/{proposal['id']}/approve", json={}, headers=_strong(client, "ava")
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "approved"
    assert not fresh_db.query_one("SELECT id FROM memories WHERE id = ?", (m["id"],))
    row = fresh_db.query_one("SELECT * FROM pending_changes WHERE id = ?", (proposal["id"],))
    assert (row["proposed_by"], row["requested_by"], row["reviewed_by"]) == (
        "scribe",
        "ava",
        "ava",
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


def test_an_agent_memory_from_a_turn_stays_with_the_person_who_drove_it(
    client, fresh_db, monkeypatch
):
    """The agent tool filed a memory for whoever the model named, or for the
    whole team, and its text went to a team notice, the shared review queue
    and the activity log, which outlives `forget`."""
    from app import config
    from app.agents import identity
    from app.services import admin, memory, users
    from app.tools.memory import remember

    monkeypatch.setattr(config, "AGENT_REVIEW", True)

    users.ensure_user("scribe", kind="agent")
    for name in ("alice", "bob"):
        users.ensure_user(name)
    agent = identity.set_agent_identity("scribe")
    try:
        with _turn("alice"):
            proposal = json.loads(remember(content="ZZHEALTHZZ note", about_user="bob"))
    finally:
        identity.reset_agent_identity(agent)
    assert proposal["note"] == "queued for human review"
    bob = _strong(client, "bob")
    assert all(
        "ZZHEALTHZZ" not in json.dumps(r) for r in client.get("/api/review", headers=bob).json()
    )
    assert "ZZHEALTHZZ" not in json.dumps(fresh_db.query("SELECT * FROM notifications"))
    approved = client.post(
        f"/api/review/{proposal['id']}/approve", json={}, headers=_strong(client, "alice")
    )
    assert approved.status_code == 200, approved.text
    assert fresh_db.query_one('SELECT "user" FROM memories')["user"] == "alice"
    assert "ZZHEALTHZZ" not in memory.memory_prompt("bob")
    assert "ZZHEALTHZZ" not in json.dumps(fresh_db.query("SELECT detail FROM activity"))
    settled = client.get("/api/review", params={"status": "approved"}, headers=bob).json()
    assert all("ZZHEALTHZZ" not in json.dumps(r) for r in settled)
    exported = admin.export(actor="ops")
    assert "ZZHEALTHZZ" not in Path(exported["path"]).read_text()


def test_a_room_agent_search_reads_no_addressed_memory(fresh_db):
    """A room turn runs as the member who called the agent, and the agent's
    answer goes to every member: reading that member's addressed memories
    repeated them to the room."""
    from app.agents import identity
    from app.services import memory, users
    from app.tools.platform import search_workspace

    memory.remember("ZZROOMZZ therapy on thursdays", user="alice", actor="alice")
    requester = identity.set_requester_identity("alice")
    room = identity.set_workspace_only_tools(True)
    try:
        hits = json.loads(search_workspace(query="ZZROOMZZ"))
    finally:
        identity._workspace_only_tools.reset(room)
        identity.reset_requester_identity(requester)
    assert [h for h in hits if h["entity"] == "memory"] == []
    # the room prompt recalls under this name, so nobody may hold it
    with pytest.raises(ValueError):
        users.ensure_human_identity("shared-chat")


def test_the_addressee_approves_her_own_memory_under_separated_review(
    client, fresh_db, monkeypatch
):
    """Only the addressee may read an addressed memory's proposal, so with
    SKEIN_REVIEW_SEPARATION refusing her approval it waited forever."""
    from app import config
    from app.agents import identity
    from app.services import users
    from app.tools.memory import remember

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    monkeypatch.setattr(config, "REVIEW_SEPARATION", True)
    users.ensure_user("scribe", kind="agent")
    users.ensure_user("ava")
    agent = identity.set_agent_identity("scribe")
    try:
        with _turn("ava"):
            proposal = json.loads(remember(content="ZZSEPARATEDZZ"))
    finally:
        identity.reset_agent_identity(agent)
    approved = client.post(
        f"/api/review/{proposal['id']}/approve", json={}, headers=_strong(client, "ava")
    )
    assert approved.status_code == 200, approved.text


def test_every_producer_files_an_addressed_memory_privately(client, fresh_db, monkeypatch):
    """The MCP server's remember passed no owner, so its proposal was reviewed
    at the workspace tier with a team "Review needed" notice quoting it, and
    readers that selected neither payload nor result_id showed its rejection."""
    from app import config
    from app.agents import identity
    from app.services import review, scope, users
    from app.tools._gate import gated_write

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scribe", kind="agent")
    users.ensure_user("ava")
    agent = identity.set_agent_identity("scribe")
    try:
        # the shape mcp_server.remember files: no requester, user named
        out = json.loads(
            gated_write(
                "memory",
                "create",
                {"content": "ZZMCPZZ diagnosis", "user": "ava"},
                lambda: {"id": 0},
                summary="remember: ZZMCPZZ diagnosis",
            )
        )
        # addressed to an agent: no person to own it, the team reviews it
        to_agent = json.loads(
            gated_write(
                "memory",
                "create",
                {"content": "agent note", "user": "scribe"},
                lambda: {"id": 0},
                summary="remember: agent note",
            )
        )
    finally:
        identity.reset_agent_identity(agent)
    row = fresh_db.query_one("SELECT * FROM pending_changes WHERE id = ?", (out["id"],))
    assert (row["review_visibility"], row["review_owner"]) == ("private", "ava")
    assert "ZZMCPZZ" not in json.dumps(fresh_db.query("SELECT * FROM notifications"))
    other = fresh_db.query_one("SELECT * FROM pending_changes WHERE id = ?", (to_agent["id"],))
    assert other["review_visibility"] == "workspace"
    # filed at the workspace tier the way the MCP server filed it before
    legacy = review.propose_change(
        "memory",
        "create",
        {"content": "ZZMCPZZ older", "user": "ava"},
        "remember: ZZMCPZZ older",
        actor="scribe",
    )
    review.reject_change(legacy["id"], "ZZREASONZZ", actor="ava", viewer=scope.Viewer("ava", True))
    stats = client.get("/api/review/stats", headers=_strong(client, "bob")).text
    assert "ZZMCPZZ" not in stats and "ZZREASONZZ" not in stats


def test_a_weak_requesters_personal_review_is_refused_not_stranded(fresh_db, monkeypatch):
    """A trusted-header name with no key reads no private row, and a memory
    about a person is theirs alone (review._addressed). Filed, the review was
    listed to nobody while the agent said "queued for human review"."""
    from app import config
    from app.services import memory, users
    from app.tools.memory import forget_memory, remember

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("mira")
    kept = memory.remember("ZZKEPTZZ", user="mira", actor="mira")
    with _turn("mira", strong=False):
        filed = json.loads(remember("ZZWEAKZZ standing context"))
        forgot = json.loads(forget_memory(int(kept["id"])))
    assert "strong identity" in filed["error"]
    assert "strong identity" in forgot["error"]
    assert fresh_db.query("SELECT id FROM pending_changes") == []


def test_the_export_keeps_an_agents_own_memories(fresh_db):
    """Over stdio the MCP server addresses a memory to the agent itself. That
    is no person's private data, and an export that dropped it lost it."""
    from app.services import admin, memory, users

    users.ensure_user("scout", kind="agent")
    memory.remember("ZZAGENTNOTEZZ", user="scout", actor="scout")
    memory.remember("ZZPERSONZZ", user="ava", actor="ava")
    exported = Path(admin.export(actor="ops")["path"]).read_text()
    assert "ZZAGENTNOTEZZ" in exported and "ZZPERSONZZ" not in exported


def test_approver_groups_govern_team_memories_and_an_addressee_judges_their_own(
    fresh_db, monkeypatch
):
    """With policy naming approver groups, an addressed memory went to the
    team review and every teammate's notice quoted it, while _addressed hid it
    from the approvers and the addressee was not qualified to judge it."""
    from app import config
    from app.agents import identity
    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.extensions.policy import reset_policy_engine, set_policy_engine
    from app.extensions.registry import ExtensionRegistry
    from app.services import review, scope, users
    from app.tools._gate import gated_write

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scribe", kind="agent")
    users.ensure_user("ava")
    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="acme.workplace",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.7.0",
                policies=(
                    PolicyContribution(
                        "acme.workplace.memory-review",
                        lambda request: PolicyDecision(
                            PolicyEffect.REVIEW, approver_groups=("leads",)
                        ),
                    ),
                ),
            ),
        )
    )
    token = set_policy_engine(registry.policy_engine)
    agent = identity.set_agent_identity("scribe")
    try:
        filed = [
            json.loads(
                gated_write(
                    "memory",
                    "create",
                    {"content": text, "user": user},
                    lambda: {"id": 0},
                    summary=f"remember: {text}",
                )
            )["id"]
            for text, user in (("ZZPERSONALZZ", "ava"), ("ZZTEAMZZ", ""))
        ]
    finally:
        identity.reset_agent_identity(agent)
        reset_policy_engine(token)
    personal, team = filed
    row = fresh_db.query_one("SELECT * FROM pending_changes WHERE id = ?", (personal,))
    assert (row["review_visibility"], row["review_owner"]) == ("private", "ava")
    notices = json.dumps(fresh_db.query("SELECT message FROM notifications"))
    assert "ZZPERSONALZZ" not in notices and "ZZTEAMZZ" in notices
    ava = scope.Viewer("ava", True)
    approved = review.approve_change(personal, actor="ava", viewer=ava, policy_registry=registry)
    assert approved["status"] == "approved"
    with pytest.raises(PermissionError, match="configured workplace approver"):
        review.approve_change(team, actor="ava", viewer=ava, policy_registry=registry)


def test_a_team_memory_is_shared_on_purpose_and_a_teammate_admits_it(client, fresh_db):
    """Nothing let a person put a fact before the whole team once `/remember`
    and the agent tool addressed memories to the speaker, and the only team
    path (an engagement memory) let its author approve it alone."""
    from app.services import memory, users

    for name in ("ava", "bob"):
        users.ensure_user(name)
    ava, bob = _strong(client, "ava"), _strong(client, "bob")
    mine = memory.remember("ZZMINEZZ reviews after lunch", user="ava", actor="ava")["id"]
    assert client.post(f"/api/memories/{mine}/share", headers=bob).status_code == 404
    assert client.post(f"/api/memories/{mine}/share").status_code in (401, 403)
    shared = client.post(f"/api/memories/{mine}/share", headers=ava)
    assert shared.status_code == 200
    again = client.post(f"/api/memories/{mine}/share", headers=ava)
    assert again.status_code == 400 and "already waits" in again.json()["detail"]
    pid = shared.json()["id"]
    assert pid in [row["id"] for row in client.get("/api/review", headers=bob).json()]
    refused = client.post(f"/api/review/{pid}/approve", json={}, headers=ava)
    assert refused.status_code == 403
    assert client.post(f"/api/review/{pid}/approve", json={}, headers=bob).status_code == 200
    rows = fresh_db.query("SELECT id, \"user\" FROM memories WHERE content LIKE 'ZZMINEZZ%'")
    assert [row["user"] for row in rows] == [""] and rows[0]["id"] != mine
    assert "ZZMINEZZ" in json.dumps(client.get("/api/memories", headers=bob).json())

    with client.stream(
        "POST",
        "/api/chat",
        json={"thread_id": "t", "message": "/remember team: ZZTEAMZZ demos on Friday"},
        headers=ava,
    ) as resp:
        out = resp.read().decode()
    assert "Another teammate approves it" in out
    proposal = fresh_db.query_one(
        "SELECT review_visibility, requested_by FROM pending_changes WHERE payload LIKE '%ZZTEAMZZ%'"
    )
    assert proposal == {"review_visibility": "workspace", "requested_by": "ava"}
    assert not fresh_db.query("SELECT id FROM memories WHERE content LIKE '%ZZTEAMZZ%'")
    from app.services.fieldguide import PREDICATES

    assert PREDICATES["team_memory"]("ava") and not PREDICATES["team_memory"]("bob")


def test_a_privately_reviewed_forget_of_a_team_memory_keeps_its_approvers(fresh_db):
    """The approver-group exemption read the REVIEW tier, so a forget of a
    team memory, reviewed privately for its requester, skipped the groups a
    policy names: the requester deleted a team memory alone."""
    from app.services import memory, review, scope, users

    users.ensure_user("ava")
    team = memory.remember("ZZTEAMFACTZZ", actor="mira")["id"]
    change = review.propose_change(
        "memory_forget",
        "update",
        {},
        entity_id=team,
        actor="agent",
        requested_by="ava",
        approver_groups=("leads",),
        review_visibility=scope.PRIVATE,
        review_owner="ava",
    )
    row = fresh_db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change["id"],))
    with pytest.raises(PermissionError, match="configured workplace approver"):
        review._check_policy_approver(row, (), ())
    mine = memory.remember("ZZMINEZZ", user="ava", actor="ava")["id"]
    change = review.propose_change(
        "memory_forget",
        "update",
        {},
        entity_id=mine,
        actor="agent",
        requested_by="ava",
        approver_groups=("leads",),
        review_visibility=scope.PRIVATE,
        review_owner="ava",
    )
    row = fresh_db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change["id"],))
    assert review._check_policy_approver(row, (), ())["matched_groups"] == []


def test_an_mcp_agent_can_have_its_own_memory_forgotten(fresh_db):
    """Over stdio a memory is addressed to the agent itself. No page lists it
    (recall reads by person) and no MCP tool could remove it, so a wrong one
    steered the agent for good."""
    from app import mcp_server
    from app.extensions.core import core_module
    from app.extensions.registry import ExtensionRegistry
    from app.services import memory, review, scope, users

    users.ensure_user("mcp-agent", kind="agent")
    users.ensure_user("ops")
    kept = memory.remember("ZZSTALEZZ wrong fact", user="mcp-agent", actor="mcp-agent")
    token = mcp_server._current_actor.set("mcp-agent")
    try:
        filed = json.loads(mcp_server.forget_memory(kept["id"]))
    finally:
        mcp_server._current_actor.reset(token)
    assert filed["status"] == "pending"
    review.approve_change(
        filed["id"],
        actor="ops",
        strong=True,
        viewer=scope.Viewer("ops", True),
        policy_registry=ExtensionRegistry.build((core_module(),)),
    )
    assert memory.get_memory(kept["id"], user="mcp-agent") is None
