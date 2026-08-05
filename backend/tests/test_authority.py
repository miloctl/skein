"""The authority matrix and trust scores: the gate, half-life, promotion and demotion, and the walls that keep agents out of their own levels."""

import pytest
from conftest import _strong


def test_authority_review_files_promotion_and_applies(client, fresh_db, monkeypatch):
    from app.services import delegation, review, users

    # 5 strong-verdict approvals for scribe on note -> promotion proposal
    users.ensure_user("scribe", kind="agent")
    headers = _strong(client)
    for i in range(5):
        p = review.propose_change(
            "note", "create", {"topic": f"t{i}", "content": "c"}, actor="scribe"
        )
        client.post(f"/api/review/{p['id']}/approve", json={}, headers=headers)
    out = delegation.review_authority(actor="scheduler")
    assert out["filed"] == 1
    # idempotent while pending
    assert delegation.review_authority(actor="scheduler")["filed"] == 0
    pending = client.get("/api/review?status=pending").json()
    auth = next(p for p in pending if p["entity"] == "authority")
    client.post(f"/api/review/{auth['id']}/approve", json={}, headers=headers)
    assert delegation.authority_level("scribe", "note") == "notify"


def test_authority_verdicts_need_strong_human_identity(client, fresh_db):
    from app.services import review, users

    users.ensure_user("scribe", kind="agent")
    p = review.propose_change(
        "authority",
        "create",
        {"agent": "scribe", "entity": "note", "level": "notify"},
        actor="scheduler",
    )
    weak = client.post(f"/api/review/{p['id']}/approve", json={})
    assert weak.status_code == 400 and "strong identity" in weak.json()["detail"]
    # the dep now refuses the weak agent header outright (403) — the service
    # guard behind it ("judged by humans") stays as defense in depth
    as_agent = client.post(f"/api/review/{p['id']}/approve", json={}, headers={"X-User": "scribe"})
    assert as_agent.status_code == 403 and "agent identity" in as_agent.json()["detail"]
    ok = client.post(f"/api/review/{p['id']}/approve", json={}, headers=_strong(client))
    assert ok.json()["status"] == "approved"


def test_stale_authority_proposal_never_lifts_forbidden(client, fresh_db):
    from app.services import delegation, review, users

    users.ensure_user("scribe", kind="agent")
    p = review.propose_change(
        "authority",
        "create",
        {"agent": "scribe", "entity": "note", "level": "notify", "expected_current": "review"},
        actor="scheduler",
    )
    delegation.set_authority("scribe", "note", "forbidden", actor="mira")
    r = client.post(f"/api/review/{p['id']}/approve", json={}, headers=_strong(client))
    assert r.status_code == 400 and "stale" in r.json()["detail"]
    assert delegation.authority_level("scribe", "note") == "forbidden"


def test_authority_demotion_end_to_end(client, fresh_db):
    from app.services import delegation, review, users

    users.ensure_user("scribe", kind="agent")
    delegation.set_authority("scribe", "note", "notify", actor="mira")
    headers = _strong(client)
    for i in range(3):
        p = review.propose_change(
            "note", "create", {"topic": f"bad{i}", "content": "c"}, actor="scribe"
        )
        client.post(f"/api/review/{p['id']}/reject", json={"note": "off"}, headers=headers)
    out = delegation.review_authority(actor="scheduler")
    assert out["filed"] == 1
    pending = client.get("/api/review?status=pending").json()
    auth = next(c for c in pending if c["entity"] == "authority")
    assert "notify -> review" in auth["summary"]
    client.post(f"/api/review/{auth['id']}/approve", json={}, headers=headers)
    assert delegation.authority_level("scribe", "note") == "review"


def test_authority_review_skips_humans_and_meta_entities(client, fresh_db):
    from app.services import delegation, review

    headers = _strong(client)
    for i in range(5):  # human proposer with a perfect streak: no proposal
        p = review.propose_change(
            "note", "create", {"topic": f"h{i}", "content": "c"}, actor="tester"
        )
        client.post(f"/api/review/{p['id']}/approve", json={}, headers=headers)
    assert delegation.review_authority(actor="scheduler")["filed"] == 0


def test_set_authority_rejects_blank_agent(client, fresh_db):
    assert (
        client.post(
            "/api/agents/authority",
            headers=_strong(),
            json={"agent": "", "entity": "task", "level": "notify"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/agents/authority",
            headers=_strong(),
            json={"agent": "   ", "entity": "task", "level": "notify"},
        ).status_code
        == 400
    )
    # no phantom agent was minted
    assert not fresh_db.query_one("SELECT * FROM users WHERE name = 'anonymous' AND kind = 'agent'")


def test_mcp_forbidden_authority_holds(client, fresh_db, monkeypatch):
    """Every MCP writer routes through gated_write now, so the kill switch is
    asserted where it is actually enforced rather than in a private helper."""
    import json

    from app import mcp_server
    from app.services import delegation

    monkeypatch.setattr(mcp_server, "ACTOR", "mcp-agent")
    delegation.set_authority("mcp-agent", "task", "forbidden", actor="tester")
    refused = json.loads(mcp_server.create_task("blocked by the kill switch"))
    assert "forbidden" in refused.get("error", "")
    allowed = json.loads(mcp_server.log_decision("d", "text"))  # default review passes
    assert "forbidden" not in str(allowed)


def test_authority_not_self_serviceable_by_agents(client, fresh_db):
    from app.services import delegation, users

    users.ensure_user("sneaky", kind="agent")
    with pytest.raises(ValueError, match="humans"):
        delegation.set_authority("task-bot", "task", "autonomous", actor="sneaky")
    with pytest.raises(ValueError):
        delegation.set_authority("planner", "task", "autonomous", actor="planner")
    out = delegation.set_authority("task-bot", "task", "notify", actor="tester")
    assert out["level"] == "notify"  # humans still can


def test_authority_matrix_gate(client, fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.tools.portfolio import add_promise

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    # default 'review' → proposal
    out = j.loads(add_promise(promise="p1"))
    assert out.get("note") == "queued for human review"

    # autonomous → direct write even with review mode on
    client.post(
        "/api/agents/authority",
        headers=_strong(),
        json={"agent": "agent", "entity": "promise", "level": "autonomous"},
    )
    out = j.loads(add_promise(promise="p2"))
    assert out.get("status") == "open"

    # forbidden → refused
    client.post(
        "/api/agents/authority",
        headers=_strong(),
        json={"agent": "agent", "entity": "promise", "level": "forbidden"},
    )
    out = j.loads(add_promise(promise="p3"))
    assert "forbidden" in out["error"]


def test_trust_scores_streak_suggestion(client, fresh_db):
    from app.services import review
    from app.services.api_keys import create_key

    # promotion streaks count only strong-identity verdicts
    headers = {"Authorization": f"Bearer {create_key('tester', 't')['key']}"}
    for i in range(5):
        p = review.propose_change(
            "note", "create", {"topic": f"t{i}", "content": "c"}, actor="scribe"
        )
        client.post(f"/api/review/{p['id']}/approve", json={}, headers=headers)
    trust = client.get("/api/agents/trust").json()
    row = next(r for r in trust if r["agent"] == "scribe")
    assert row["approved"] == 5 and row["recent_streak"] == 5
    assert "autonomous" in row["suggestion"]


def test_weak_identity_verdicts_never_suggest_promotion(client, fresh_db):
    from app.services import review

    for i in range(5):
        p = review.propose_change(
            "note", "create", {"topic": f"w{i}", "content": "c"}, actor="scribe"
        )
        client.post(f"/api/review/{p['id']}/approve", json={})  # X-User only
    row = next(r for r in client.get("/api/agents/trust").json() if r["agent"] == "scribe")
    assert row["approved"] == 5  # verdicts still count as history
    assert row["recent_streak"] == 0 and row["suggestion"] == ""


def test_authority_half_life(client, fresh_db):
    from app.services.delegation import set_authority
    from app.services.insights import run_findings

    set_authority("planner-agent", "task", "autonomous", actor="manager")
    row = fresh_db.query_row("SELECT * FROM agent_authority WHERE agent = 'planner-agent'")
    assert row["review_by"] is not None
    # not stale yet
    assert not any(f["rule_id"] == "authority_stale" for f in run_findings(actor="t")["findings"])
    fresh_db.execute(
        "UPDATE agent_authority SET review_by = '2020-01-01' WHERE agent = 'planner-agent'"
    )
    hits = [f for f in run_findings(actor="t")["findings"] if f["rule_id"] == "authority_stale"]
    assert len(hits) == 1 and "planner-agent" in hits[0]["message"]
    # forbidden/review grants carry no review_by — the kill switch never expires
    set_authority("planner-agent", "note", "forbidden", actor="manager")
    row = fresh_db.query_row(
        "SELECT review_by FROM agent_authority WHERE agent = 'planner-agent' AND entity = 'note'"
    )
    assert row["review_by"] is None


def test_authority_stale_null_review_by_falls_back(fresh_db):
    from app.services.insights import run_findings

    fresh_db.execute(
        "INSERT INTO agent_authority (agent, entity, level, updated_by, updated_at)"
        " VALUES ('old-agent', 'task', 'autonomous', 'm', '2020-01-01T00:00:00')"
    )
    hits = [f for f in run_findings(actor="t")["findings"] if f["rule_id"] == "authority_stale"]
    assert len(hits) == 1  # pre-017-style row (NULL review_by) still expires


def test_mcp_writes_route_through_the_gate(client, fresh_db, monkeypatch):
    from app import config, mcp_server

    monkeypatch.setattr(mcp_server, "ACTOR", "code-agent")
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    import json as j

    out = j.loads(mcp_server.create_task("gated task"))
    assert out.get("note") == "queued for human review"
    assert client.get("/api/tasks").json() == []
    pending = client.get("/api/review?status=pending").json()
    assert pending and pending[0]["proposed_by"] == "code-agent"

    # autonomous grant flips it to direct — and trust history exists
    from app.services import delegation

    delegation.set_authority("code-agent", "task", "autonomous", actor="tester")
    out = j.loads(mcp_server.create_task("direct task"))
    assert out.get("status") == "todo"


def test_a_generic_task_proposal_cannot_close_delegated_work(fresh_db):
    """The sponsor-bound acceptance loop was optional: an agent could file a
    plain `task` update instead of submit_for_acceptance, and ANY human could
    approve it — no sponsor binding, no reason on record, no override marking,
    and it counted as a clean approval toward promotion."""
    import pytest

    from app import db
    from app.services import delegation, review, users, work

    users.ensure_user("scout", kind="agent")
    users.ensure_user("mira")
    tid = work.create_task(title="probe", actor="mira")["id"]
    delegation.delegate_task(tid, "scout", "mira", actor="mira")

    p = review.propose_change("task", "update", {"status": "done"}, entity_id=tid, actor="scout")
    with pytest.raises(ValueError, match="delegated"):
        review.approve_change(p["id"], actor="dave")  # not the sponsor

    assert db.query_one("SELECT status FROM tasks WHERE id = ?", (tid,))["status"] != "done"


def test_forbidden_covers_the_whole_entity_family(fresh_db):
    """A grant may be fine-grained; a forbidden is absolute. Forbidding `note`
    left note_edit and note_delete at the default, so an agent blocked from
    CREATING a note could still rewrite an existing one."""
    from app.services import delegation, users
    from app.tools._gate import effective_level

    users.ensure_user("scout", kind="agent")
    users.ensure_user("mira")
    delegation.set_authority("scout", "note", "forbidden", actor="mira")

    for entity in ("note", "note_edit", "note_delete"):
        assert effective_level("scout", entity) == "forbidden", entity

    # a fine-grained GRANT on the sibling still cannot loosen the family ban
    delegation.set_authority("scout", "note_edit", "autonomous", actor="mira")
    assert effective_level("scout", "note_edit") == "forbidden"


def test_mcp_capture_gates_on_the_classified_entity(fresh_db, monkeypatch):
    """capture checked only `forbidden`, so an agent at the DEFAULT review
    level had create_task queued and `todo: …` written straight through."""
    import json

    from app import config
    from app.services import users

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("mcp-agent", kind="agent")

    from app import mcp_server
    from app.services import review, work

    out = json.loads(mcp_server.capture("todo: ungated straight into the tracker"))
    assert out.get("status") == "pending", out
    assert work.list_tasks() == []

    # and the queued proposal must be APPLICABLE. A payload the registry
    # handler cannot take made every capture fail at apply and reset to
    # pending, wedging the inbox — a gate that swallows the work is not a gate
    review.approve_change(out["id"], actor="mira")
    assert [t["title"] for t in work.list_tasks()] == ["ungated straight into the tracker"]


def test_every_classified_capture_kind_produces_an_applicable_proposal(fresh_db, monkeypatch):
    """One case per branch of capture.plan — the payload keys must be the
    registry handler's own kwargs, for every entity capture can route to."""
    import json

    from app import config, mcp_server
    from app.services import blockers, collab, intake, promises, review, users, work

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("mcp-agent", kind="agent")
    cases = [
        ("todo: ship it", lambda: work.list_tasks()),
        ("q: who owns dns?", lambda: collab.list_questions()),
        ("blocked on the vendor", lambda: blockers.list_blockers()),
        ("decision: use sqlite", lambda: collab.list_decisions()),
        ("promised: demo friday", lambda: promises.list_promises()),
        ("req: dashboards", lambda: intake.list_requests()),
        ("just a plain note", lambda: collab.search_notes("")),
    ]
    for text, rows in cases:
        out = json.loads(mcp_server.capture(text))
        assert out.get("status") == "pending", (text, out)
        review.approve_change(out["id"], actor="mira")  # must not raise
        assert rows(), text


def test_a_fine_grained_grant_is_not_defeated_by_an_absent_parent(fresh_db):
    """authority_level defaults to 'review' when no row exists, so taking the
    strictest of entity and family root made an ABSENT parent override an
    explicit child grant — every fine-grained grant became a no-op."""
    from app.services import delegation, users
    from app.tools._gate import effective_level

    users.ensure_user("scout", kind="agent")
    users.ensure_user("mira")
    delegation.set_authority("scout", "note_edit", "autonomous", actor="mira")
    assert effective_level("scout", "note_edit") == "autonomous"  # no `note` row exists

    delegation.set_authority("scout", "note", "forbidden", actor="mira")
    assert effective_level("scout", "note_edit") == "forbidden"  # the ban still wins


def test_every_registry_mutator_has_a_family_entry():
    """_FAMILY is hand-maintained; this is what stops a new <root>_<verb>
    entity from silently escaping a family-level forbidden."""
    from app.services.review import _registry
    from app.tools._gate import _FAMILY, _NOT_A_FAMILY

    roots = set(_registry())
    missing = [
        e
        for e in roots
        if "_" in e and e.rsplit("_", 1)[0] in roots and e not in _FAMILY and e not in _NOT_A_FAMILY
    ]
    assert not missing, (
        f"registry mutators with no _FAMILY entry: {missing}."
        " Add the family, or add it to _NOT_A_FAMILY with the reason."
    )
    # every declared family root must itself be a real registry entity
    assert not set(_FAMILY.values()) - roots


def test_a_level_the_gate_cannot_honour_is_refused(fresh_db):
    """_gate.py takes the review path for ALWAYS_REVIEW entities BEFORE it
    reads the level, so a stored 'autonomous' rendered "delete a note: acts
    alone" on the authority card while every such write still waited for a
    human — a false badge on the destructive rows. The level is refused now,
    so the lie has no way to be stored."""
    import pytest

    from app.services import delegation
    from app.tools._gate import ALWAYS_REVIEW

    for entity in sorted(ALWAYS_REVIEW):
        for level in ("autonomous", "notify"):
            with pytest.raises(ValueError, match="always waits for a human"):
                delegation.set_authority("planner-agent", entity, level, actor="mgr")
        # the two honest levels still work
        for level in ("review", "forbidden"):
            delegation.set_authority("planner-agent", entity, level, actor="mgr")


def test_entities_with_no_authority_are_refused_everywhere(client, fresh_db):
    """The picker hid these and set_authority did not, so a direct POST stored
    a grant the picker could not produce, for a power no agent tool reads."""
    import pytest

    from app.services import delegation

    served = client.get("/api/agents/entities").json()
    for entity in delegation.NO_AUTHORITY:
        assert entity not in served["entities"]
        with pytest.raises(ValueError, match="carries no authority level"):
            delegation.set_authority("planner-agent", entity, "autonomous", actor="mgr")


def test_the_card_learns_which_entities_always_wait(client, fresh_db):
    """The "(always)" marker must come from _gate.py, never a hand-typed
    frontend list, or the label drifts from the behaviour it describes."""
    from app.tools._gate import ALWAYS_REVIEW

    served = client.get("/api/agents/entities").json()
    assert set(served["always_review"]) == set(ALWAYS_REVIEW)
