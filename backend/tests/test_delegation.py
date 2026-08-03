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
    out = client.post(
        f"/api/tasks/{t['id']}/delegate", json={"agent": "scribe", "sponsor": "tester"}
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
    client.post(f"/api/tasks/{t['id']}/delegate", json={"agent": "helper", "sponsor": "tester"})
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
