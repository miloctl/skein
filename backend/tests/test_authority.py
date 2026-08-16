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


@pytest.mark.parametrize("name", ["anonymous", "system", "ci", "mcp", "scheduler", "team"])
def test_caller_supplied_agent_names_cannot_mint_core_subjects(client, fresh_db, name):
    from app.services import delegation, users, work

    users.ensure_user("mira")
    task = work.create_task("reserved delegate", actor="mira")
    with pytest.raises(ValueError, match=r"reserved for the system|agent name is required"):
        delegation.delegate_task(task["id"], name, sponsor="mira", actor="mira")
    with pytest.raises(ValueError, match=r"reserved for the system|agent name is required"):
        delegation.set_authority(name, "note", "forbidden", actor="mira")
    assert fresh_db.query_one("SELECT 1 FROM users WHERE name = ?", (name,)) is None


def test_mcp_forbidden_authority_holds(client, fresh_db, monkeypatch):
    """Every MCP writer routes through gated_write now, so the kill switch is
    asserted where it is actually enforced rather than in a private helper."""
    import json

    from app import mcp_server
    from app.services import delegation, wording

    monkeypatch.setattr(mcp_server, "ACTOR", "mcp-agent")
    delegation.set_authority("mcp-agent", "task", "forbidden", actor="tester")
    refused = json.loads(mcp_server.create_task("blocked by the kill switch"))
    assert refused == {"error": wording.write_policy_denied()}
    allowed = json.loads(mcp_server.log_decision("d", "text"))  # default review passes
    assert "error" not in allowed


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
    from app.services import wording
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
    assert out == {"error": wording.write_policy_denied()}
    assert fresh_db.query_one("SELECT id FROM promises WHERE promise = 'p3'") is None


def test_trust_scores_streak_suggestion(client, fresh_db):
    from app.services import review, users
    from app.services.api_keys import create_key

    # the agent has a users row on any running instance, and trust_scores
    # joins on it — humans propose too (services/ingest.py), and their
    # approval record must never reach this surface
    users.ensure_user("scribe", kind="agent")
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
    assert row["last_verified_verdict"] == "approved"
    # `notify`, because that is the rung review_authority files — a promotion
    # climbs one, and this line said `autonomous` for as long as it existed
    assert "notify" in row["suggestion"]


def test_weak_identity_verdicts_never_suggest_promotion(client, fresh_db):
    from app.services import review, users

    users.ensure_user("scribe", kind="agent")

    for i in range(5):
        p = review.propose_change(
            "note", "create", {"topic": f"w{i}", "content": "c"}, actor="scribe"
        )
        client.post(f"/api/review/{p['id']}/approve", json={})  # X-User only
    row = next(r for r in client.get("/api/agents/trust").json() if r["agent"] == "scribe")
    assert row["approved"] == 5  # verdicts still count as history
    assert row["recent_streak"] == 0 and row["suggestion"] == ""
    assert row["last_verified_verdict"] == ""


def test_trust_scores_names_the_last_verified_rejection(client):
    from app.services import review, users
    from app.services.api_keys import create_key

    users.ensure_user("scribe", kind="agent")
    p = review.propose_change("note", "create", {"topic": "x", "content": "c"}, actor="scribe")
    headers = {"Authorization": f"Bearer {create_key('tester', 't')['key']}"}
    client.post(f"/api/review/{p['id']}/reject", json={"note": "not ready"}, headers=headers)

    row = next(r for r in client.get("/api/agents/trust").json() if r["agent"] == "scribe")
    assert row["recent_streak"] == 0
    assert row["last_verified_verdict"] == "rejected"


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
    from app.services import delegation
    from app.services.insights import run_findings

    fresh_db.execute(
        "INSERT INTO agent_authority (agent, entity, level, updated_by, updated_at)"
        " VALUES ('old-agent', 'task', 'autonomous', 'm', '2020-01-01T00:00:00')"
    )
    hits = [f for f in run_findings(actor="t")["findings"] if f["rule_id"] == "authority_stale"]
    assert len(hits) == 1  # pre-017-style row (NULL review_by) still expires
    grant = delegation.authority_status("old-agent", "task")
    assert grant["review_by"] == "2020-03-31"
    assert grant["effective_level"] == "review" and grant["review_expired"] is True


def test_legacy_authority_fallback_uses_the_team_day(fresh_db, monkeypatch):
    from zoneinfo import ZoneInfo

    from app import config
    from app.services import delegation

    monkeypatch.setattr(config, "TZ", ZoneInfo("America/Los_Angeles"))
    fresh_db.execute(
        "INSERT INTO agent_authority (agent, entity, level, updated_by, updated_at)"
        " VALUES ('west-agent', 'task', 'autonomous', 'm', '2026-01-01T00:30:00+00:00')"
    )
    assert delegation.authority_status("west-agent", "task")["review_by"] == "2026-03-31"


def test_authority_changes_serialize_the_stale_check_and_kill_switch(fresh_db, monkeypatch):
    from threading import Event, Thread, current_thread

    from app.services import delegation

    delegation.set_authority("locked-agent", "task", "review", actor="manager")
    original = delegation.authority_level
    checked = Event()
    release = Event()
    writer_done = Event()
    errors: list[Exception] = []

    def paused_level(agent, entity):
        level = original(agent, entity)
        if current_thread().name == "authority-approval":
            checked.set()
            assert release.wait(5)
        return level

    def approve():
        try:
            delegation.set_authority(
                "locked-agent",
                "task",
                "autonomous",
                "review",
                actor="manager",
            )
        except Exception as exc:  # pragma: no cover — asserted empty below
            errors.append(exc)

    def forbid():
        try:
            delegation.set_authority("locked-agent", "task", "forbidden", actor="manager")
        except Exception as exc:  # pragma: no cover — asserted empty below
            errors.append(exc)
        finally:
            writer_done.set()

    monkeypatch.setattr(delegation, "authority_level", paused_level)
    approval = Thread(target=approve, name="authority-approval")
    writer = Thread(target=forbid, name="authority-kill-switch")
    approval.start()
    assert checked.wait(5)
    writer.start()
    assert not writer_done.wait(0.1), "the kill switch bypassed the authority-pair lock"
    release.set()
    approval.join(5)
    writer.join(5)

    assert errors == []
    assert delegation.authority_level("locked-agent", "task") == "forbidden"


def test_expired_authority_forces_review_even_when_the_gate_is_off(client, fresh_db, monkeypatch):
    import json

    from app import config, db
    from app.services import delegation
    from app.tools.portfolio import add_promise

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    delegation.set_authority("agent", "promise", "autonomous", actor="tester")
    fresh_db.execute(
        "UPDATE agent_authority SET review_by = ? WHERE agent = 'agent' AND entity = 'promise'",
        (db.today().isoformat(),),
    )
    direct = json.loads(add_promise(promise="still inside the review date"))
    assert direct["status"] == "open"

    fresh_db.execute(
        "UPDATE agent_authority SET review_by = '2020-01-01'"
        " WHERE agent = 'agent' AND entity = 'promise'"
    )
    queued = json.loads(add_promise(promise="past the review date"))
    assert queued["note"] == "queued for human review"
    assert not fresh_db.query_one("SELECT id FROM promises WHERE promise = 'past the review date'")

    row = delegation.authority_matrix("agent")[0]
    assert row["level"] == "autonomous"
    assert row["effective_level"] == "review"
    assert row["review_expired"] is True
    assert row["review_by"] == "2020-01-01"

    from app.services import review

    for i in range(5):
        proposal = review.propose_change(
            "promise",
            "create",
            {"promise": f"reviewed {i}"},
            actor="agent",
        )
        client.post(
            f"/api/review/{proposal['id']}/approve",
            json={},
            headers=_strong(client),
        )
    trust = next(
        row
        for row in delegation.trust_scores()
        if row["agent"] == "agent" and row["entity"] == "promise"
    )
    assert trust["current_level"] == "autonomous"
    assert trust["effective_level"] == "review"
    assert delegation.review_authority(actor="scheduler")["filed"] == 0

    delegation.set_authority("agent", "promise", "autonomous", actor="tester")
    renewed = delegation.authority_matrix("agent")[0]
    assert renewed["effective_level"] == "autonomous"
    assert renewed["review_expired"] is False


def test_expired_matrix_grant_does_not_cancel_a_sponsor_delegation(fresh_db):
    from app.services import delegation, users, work

    users.ensure_user("mira")
    task = work.create_task(title="delegated exception", actor="mira")
    delegation.delegate_task(task["id"], "scout", "mira", actor="mira")
    delegation.set_authority("scout", "task", "autonomous", actor="mira")
    fresh_db.execute(
        "UPDATE agent_authority SET review_by = '2020-01-01'"
        " WHERE agent = 'scout' AND entity = 'task'"
    )

    assert delegation.authority_status("scout", "task")["review_expired"] is True
    assert delegation.claim_task(task["id"], actor="scout")["status"] == "in_progress"


def test_only_an_administrator_can_approve_an_authority_change(client, fresh_db, monkeypatch):
    from app import config
    from app.services import delegation, review

    monkeypatch.setattr(config, "ADMINS", ("admin",))
    proposal = review.propose_change(
        "authority",
        "create",
        {"agent": "scout", "entity": "promise", "level": "autonomous"},
        actor="scheduler",
        notify_team=False,
    )

    refused = client.post(
        f"/api/review/{proposal['id']}/approve",
        json={},
        headers=_strong(client, "mira"),
    )
    assert refused.status_code == 403
    assert delegation.authority_matrix("scout") == []

    approved = client.post(
        f"/api/review/{proposal['id']}/approve",
        json={},
        headers=_strong(client, "admin"),
    )
    assert approved.json()["status"] == "approved"
    assert delegation.authority_matrix("scout")[0]["level"] == "autonomous"


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
    from app.tools._gate import effective_authority

    users.ensure_user("scout", kind="agent")
    users.ensure_user("mira")
    delegation.set_authority("scout", "note", "forbidden", actor="mira")

    for entity in ("note", "note_edit", "note_delete"):
        assert effective_authority("scout", entity)[0] == "forbidden", entity

    # a fine-grained GRANT on the sibling still cannot loosen the family ban
    delegation.set_authority("scout", "note_edit", "autonomous", actor="mira")
    assert effective_authority("scout", "note_edit")[0] == "forbidden"


def test_mcp_capture_gates_on_the_classified_entity(fresh_db, monkeypatch):
    """capture checked only `forbidden`, so an agent at the DEFAULT review
    level had create_task queued and `todo: …` written straight through."""
    import json

    from app import config
    from app.services import users

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("mcp-agent", kind="agent")

    from app import mcp_server
    from app.main import create_app
    from app.services import review, work

    out = json.loads(mcp_server.capture("todo: ungated straight into the tracker"))
    assert out.get("status") == "pending", out
    assert work.list_tasks() == []

    # and the queued proposal must be APPLICABLE. A payload the registry
    # handler cannot take made every capture fail at apply and reset to
    # pending, wedging the inbox — a gate that swallows the work is not a gate
    review.approve_change(
        out["id"], actor="mira", policy_registry=create_app().state.skein_registry
    )
    assert [t["title"] for t in work.list_tasks()] == ["ungated straight into the tracker"]


def test_every_classified_capture_kind_produces_an_applicable_proposal(fresh_db, monkeypatch):
    """One case per branch of capture.plan — the payload keys must be the
    registry handler's own kwargs, for every entity capture can route to."""
    import json

    from app import config, mcp_server
    from app.main import create_app
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
        review.approve_change(
            out["id"], actor="mira", policy_registry=create_app().state.skein_registry
        )  # must not raise
        assert rows(), text


def test_a_fine_grained_grant_is_not_defeated_by_an_absent_parent(fresh_db):
    """authority_level defaults to 'review' when no row exists, so taking the
    strictest of entity and family root made an ABSENT parent override an
    explicit child grant — every fine-grained grant became a no-op."""
    from app.services import delegation, users
    from app.tools._gate import effective_authority

    users.ensure_user("scout", kind="agent")
    users.ensure_user("mira")
    delegation.set_authority("scout", "note_edit", "autonomous", actor="mira")
    assert effective_authority("scout", "note_edit")[0] == "autonomous"  # no `note` row exists

    delegation.set_authority("scout", "note", "forbidden", actor="mira")
    assert effective_authority("scout", "note_edit")[0] == "forbidden"  # the ban still wins


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


def test_the_suggestion_names_the_rung_the_code_actually_files(client, fresh_db):
    """`review_authority` climbs one rung to `notify`. The suggestion said
    `autonomous`, and skipped promotion_blocked entirely — so it offered a
    promotion on task_completion, which is in NO_AUTHORITY and can never be
    filed, and is the entity a delegated agent proposes on most."""
    from app.services import delegation, review, users
    from app.services.api_keys import create_key

    users.ensure_user("scribe", kind="agent")
    headers = {"Authorization": f"Bearer {create_key('tester', 't')['key']}"}
    for i in range(delegation.TRUST_STREAK):
        p = review.propose_change(
            "note", "create", {"topic": f"n{i}", "content": "c"}, actor="scribe"
        )
        client.post(f"/api/review/{p['id']}/approve", json={}, headers=headers)
    row = next(r for r in delegation.trust_scores() if r["entity"] == "note")
    assert "notify" in row["suggestion"]
    assert "autonomous" not in row["suggestion"]

    # settled rows written straight in: task_completion proposals cannot be
    # APPLIED against a task that does not exist, and the apply path is not
    # what this pins — the streak is read from the table either way
    for _ in range(delegation.TRUST_STREAK):
        fresh_db.execute(
            "INSERT INTO pending_changes (entity, action, payload, summary, proposed_by,"
            " origin, status, reviewed_by, reviewed_at, created_at, reviewed_strong,"
            " reviewed_override) VALUES ('task_completion', 'update', '{}', 's', 'scribe',"
            " 'agent', 'approved', 'ava', ?, ?, 1, 0)",
            (fresh_db.now(), fresh_db.now()),
        )
    blocked = next(r for r in delegation.trust_scores() if r["entity"] == "task_completion")
    assert blocked["recent_streak"] >= delegation.TRUST_STREAK, "the fixture did not build a streak"
    assert delegation.promotion_blocked("scribe", "task_completion", blocked["current_level"])
    assert blocked["suggestion"] == "", "a promotion that could never be filed"
