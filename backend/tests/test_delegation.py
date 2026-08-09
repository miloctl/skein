"""The delegation work loop: claim, worklog, submit for acceptance, and the walls that stop an agent closing its own task."""

import pytest
from conftest import _delegated_task, _strong


def test_delegation_work_loop_end_to_end(client, fresh_db, monkeypatch):
    from app import config
    from app.services import delegation, users, work

    monkeypatch.setattr(config, "AGENT_REVIEW", False)  # loop must gate regardless
    users.ensure_user("mira")
    t = work.create_task(title="build the probe", actor="mira")
    delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
    delegation.claim_task(t["id"], actor="scout")
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (t["id"],))["status"] == (
        "in_progress"
    )
    delegation.report_progress(t["id"], "probe scaffolded, tests next", actor="scout")
    assert client.get(f"/api/tasks/{t['id']}/worklog").json()[0]["note"].startswith("probe")
    out = delegation.submit_completion(t["id"], "probe built and green", actor="scout")
    # still not done — the sponsor's verdict is the acceptance
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (t["id"],))["status"] == (
        "in_progress"
    )
    r = client.post(
        f"/api/review/{out['proposal_id']}/approve", json={}, headers=_strong(client, "mira")
    )
    assert r.json()["status"] == "approved"
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (t["id"],))["status"] == (
        "done"
    )
    notes = [w["note"] for w in delegation.list_worklog(t["id"])]
    assert any(n.startswith("[accepted]") for n in notes)


def test_claim_requires_the_delegated_agent(fresh_db):
    from app.services import delegation, users, work

    users.ensure_user("mira")
    t = work.create_task(title="x", actor="mira")
    delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
    try:
        delegation.claim_task(t["id"], actor="other-agent")
        raise AssertionError("claim by a non-delegate succeeded")
    except ValueError:
        pass


def test_worklog_writes_bound_to_the_loop(fresh_db):
    from app.services import delegation, users

    tid = _delegated_task(fresh_db)
    users.ensure_user("intruder", kind="agent")
    with pytest.raises(ValueError, match="delegate or sponsor"):
        delegation.report_progress(tid, "[accepted] looks done to me", actor="intruder")
    with pytest.raises(ValueError, match="2000"):
        delegation.report_progress(tid, "x" * 2001, actor="scout")
    # the sponsor may annotate; a done task's worklog is frozen
    delegation.report_progress(tid, "sponsor context", actor="mira")
    delegation.accept_completion(tid, "fine", actor="scout")
    with pytest.raises(ValueError, match="history"):
        delegation.report_progress(tid, "late addendum", actor="scout")


def test_agent_cannot_self_complete_delegated_task(client, fresh_db):
    from app.services import work

    tid = _delegated_task(fresh_db)
    with pytest.raises(ValueError, match="sponsor's verdict"):
        work.update_task(tid, status="done", actor="scout", origin="agent")
    # a human closing it directly stays allowed (sponsor override)
    r = client.patch(f"/api/tasks/{tid}", json={"status": "done"})
    assert r.status_code == 200


def test_submit_completion_dedupes_pending(fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    delegation.submit_completion(tid, "round one", actor="scout")
    with pytest.raises(ValueError, match="already awaits acceptance"):
        delegation.submit_completion(tid, "round one again", actor="scout")


def test_rejection_keeps_task_open_and_feeds_trust(client, fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    out = delegation.submit_completion(tid, "done, trust me", actor="scout")
    r = client.post(
        f"/api/review/{out['proposal_id']}/reject",
        json={"note": "tests are red"},
        headers=_strong(client, "mira"),
    )
    assert r.json()["status"] == "rejected"
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (tid,))["status"] == (
        "in_progress"
    )
    row = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert row["rejection_streak"] == 1
    # resubmission after the fix is open
    delegation.submit_completion(tid, "fixed and green", actor="scout")


def test_agent_cannot_delegate_to_itself(fresh_db):
    from app.services import users, work

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    t = work.create_task(title="mine now", actor="mira")
    from app.services import delegation

    with pytest.raises(ValueError, match="itself"):
        delegation.delegate_task(t["id"], "scout", "mira", actor="scout", origin="agent")


def test_delegate_task_and_inbox(client, fresh_db):
    t = client.post("/api/tasks", json={"title": "agent work"}).json()
    # a key: 'scribe' does not exist yet, and MINTING an agent identity takes
    # the scarce credential (routes/api.py::post_delegate)
    out = client.post(
        f"/api/tasks/{t['id']}/delegate",
        json={"agent": "scribe", "sponsor": "tester"},
        headers=_strong(),
    ).json()
    assert out["delegated_agent"] == "scribe"
    users = {u["name"]: u for u in client.get("/api/users").json()}
    assert users["scribe"]["kind"] == "agent"

    inbox = client.get("/api/agents/scribe/inbox").json()
    assert [x["id"] for x in inbox["delegated_tasks"]] == [t["id"]]

    mc = client.get("/api/agents").json()
    scribe = next(a for a in mc if a["agent"] == "scribe")
    assert scribe["open_tasks"] == 1


def test_reassign_ends_delegation(client, fresh_db):
    t = client.post("/api/tasks", json={"title": "work"}).json()
    client.post(
        f"/api/tasks/{t['id']}/delegate",
        json={"agent": "helper", "sponsor": "tester"},
        headers=_strong(),
    )
    client.patch(f"/api/tasks/{t['id']}", json={"assignee": "zoe"})
    row = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["delegated_agent"] == "" and row["sponsor"] == ""
    mc = client.get("/api/agents").json()
    helper = next(a for a in mc if a["agent"] == "helper")
    assert helper["open_tasks"] == 0


def test_agent_inbox_unknown_agent_is_an_error(client):
    assert client.get("/api/agents/definitely-a-typo/inbox").status_code == 404


def test_agent_delegated_done_proposal_auto_rejects_not_wedges(client, fresh_db):
    """A generic task/done proposal on an agent's OWN delegated task can never
    be approved into success. It must settle as rejected, not reset to pending
    where it would clutter /review until a human rejects it by hand."""
    from app.services import review

    tid = _delegated_task(fresh_db)
    p = review.propose_change("task", "update", {"status": "done"}, entity_id=tid, actor="scout")
    with pytest.raises(ValueError, match="auto-rejected"):
        review.approve_change(p["id"], actor="mira")
    row = fresh_db.query_one("SELECT status FROM pending_changes WHERE id = ?", (p["id"],))
    assert row["status"] == "rejected"  # settled, not boomeranged to pending
    # and the task itself stayed open — the escape is still closed
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (tid,))["status"] != "done"


def test_the_trust_read_never_reports_a_human(client):
    """Humans are in `pending_changes` too — services/ingest.py files every
    pasted line under the person who pasted it — so an unfiltered read put one
    teammate's approval rate and rejection streak in front of the whole
    roster. The filter belongs in the service, not in a caller."""
    from app.services import delegation, review, users

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    client.post(
        "/api/ingest",
        json={"text": "todo: ship it\ntodo: write it"},
        headers={"X-User": "mira"},
    )
    p = review.propose_change("task", "create", {"title": "from an agent"}, actor="scout")
    for row in review.list_changes("pending"):
        review.approve_change(row["id"], actor="ava")
    assert p

    rows = delegation.trust_scores()
    assert {r["agent"] for r in rows} == {"scout"}
    # and the route that renders it agrees
    served = client.get("/api/agents/trust").json()
    assert "mira" not in {r["agent"] for r in served}


def test_the_trust_read_scans_the_authority_proposals_once(client):
    """`promotion_blocked` reads every authority proposal, and that table is
    unindexed for this query (the index is on (proposed_by, entity)). Called
    per row it turned the Approvals page from 122 queries into 202 the moment
    agents started earning streaks — the N+1 this module removed from
    trust_scores, reintroduced one function over. The short-circuit hides it
    exactly while the trust program is not working.
    """
    from app import db
    from app.services import delegation, users

    for i in range(20):
        users.ensure_user(f"agent{i}", kind="agent")
        for entity in ("note", "task"):
            for _ in range(delegation.TRUST_STREAK):
                db.execute(
                    "INSERT INTO pending_changes (entity, entity_id, action, payload, summary,"
                    " proposed_by, origin, status, reviewed_by, reviewed_at, created_at,"
                    " reviewed_strong, reviewed_override) VALUES (?, 1, 'create', '{}', 's', ?,"
                    " 'agent', 'approved', 'ava', ?, ?, 1, 0)",
                    (entity, f"agent{i}", db.now(), db.now()),
                )

    seen = []
    real = db.query

    def counting(sql, params=()):
        seen.append(sql)
        return real(sql, params)

    db.query = counting
    try:
        rows = delegation.trust_scores()
    finally:
        db.query = real

    assert len(rows) == 40
    assert sum(1 for r in rows if r["suggestion"]) == 40, "every pair must be promotable here"
    scans = [s for s in seen if "entity = 'authority'" in s]
    assert len(scans) == 1, f"{len(scans)} scans for {len(rows)} pairs — the N+1 is back"
